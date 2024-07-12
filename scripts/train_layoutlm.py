import argparse
import csv
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Optional, Union, Tuple

import numpy as np
import torch
from sklearn_crfsuite import metrics
from torch.nn import CrossEntropyLoss
from torch.utils.data import DataLoader, Dataset, RandomSampler, SequentialSampler
from tqdm import tqdm
from transformers import (
    LayoutLMForTokenClassification,
    LayoutLMTokenizer,
)
from transformers.modeling_outputs import TokenClassifierOutput
from transformers.optimization import Adafactor

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
torch.manual_seed(1234)
np.random.seed(1234)


class FunsdDataset(Dataset):
    def __init__(self, args, tokenizer, labels, pad_token_label_id, mode):
        logger.info("Creating features from dataset file at %s", args.data_dir)
        examples = read_examples_from_file(args.data_dir, mode)
        features = convert_examples_to_features(
            examples,
            labels,
            args.max_seq_length,
            tokenizer,
            cls_token_at_end=bool(args.model_type in ["xlnet"]),
            # xlnet has a cls token at the end
            cls_token=tokenizer.cls_token,
            cls_token_segment_id=2 if args.model_type in ["xlnet"] else 0,
            sep_token=tokenizer.sep_token,
            sep_token_extra=bool(args.model_type in ["roberta"]),
            # roberta uses an extra separator b/w pairs of
            # sentences,
            # cf. github.com/pytorch/fairseq/commit/1684e166e3da03f5b600dbb7855cb98ddfcd0805
            pad_on_left=bool(args.model_type in ["xlnet"]),
            # pad on the left for xlnet
            pad_token=tokenizer.convert_tokens_to_ids([tokenizer.pad_token])[0],
            pad_token_segment_id=4 if args.model_type in ["xlnet"] else 0,
            pad_token_label_id=pad_token_label_id,
        )

        self.features = features
        # Convert to Tensors and build dataset
        self.all_input_ids = torch.tensor(
            [f.input_ids for f in features], dtype=torch.long
        )
        self.all_input_mask = torch.tensor(
            [f.input_mask for f in features], dtype=torch.long
        )
        self.all_segment_ids = torch.tensor(
            [f.segment_ids for f in features], dtype=torch.long
        )
        self.all_label_ids = torch.tensor(
            [f.label_ids for f in features], dtype=torch.long
        )
        self.all_bboxes = torch.tensor([f.boxes for f in features], dtype=torch.long)

    def __len__(self):
        return len(self.features)

    def __getitem__(self, index):
        return (
            self.all_input_ids[index],
            self.all_input_mask[index],
            self.all_segment_ids[index],
            self.all_label_ids[index],
            self.all_bboxes[index],
        )


class InputExample(object):
    """A single training/test example for token classification."""

    def __init__(self, guid, words, labels, boxes, actual_bboxes, file_name, page_size):
        """Constructs a InputExample.

        Args:
            guid: Unique id for the example.
            words: list. The words of the sequence.
            labels: (Optional) list. The labels for each word of the
            sequence. This should be specified for train and dev
            examples, but not for test examples.
        """
        self.guid = guid
        self.words = words
        self.labels = labels
        self.boxes = boxes
        self.actual_bboxes = actual_bboxes
        self.file_name = file_name
        self.page_size = page_size


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(
        self,
        input_ids,
        input_mask,
        segment_ids,
        label_ids,
        boxes,
        actual_bboxes,
        file_name,
        page_size,
    ):
        assert (
            0 <= all(boxes) <= 1000
        ), "Error with input bbox ({}): the coordinate value is not between 0 and 1000".format(
            boxes
        )
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_ids = label_ids
        self.boxes = boxes
        self.actual_bboxes = actual_bboxes
        self.file_name = file_name
        self.page_size = page_size


def read_examples_from_file(data_dir, mode):
    file_path = os.path.join(data_dir, "{}.txt".format(mode))
    box_file_path = os.path.join(data_dir, "{}_box.txt".format(mode))
    image_file_path = os.path.join(data_dir, "{}_image.txt".format(mode))
    guid_index = 1
    examples = []
    with open(file_path, encoding="utf-8") as f, open(
        box_file_path, encoding="utf-8"
    ) as fb, open(image_file_path, encoding="utf-8") as fi:
        words = []
        boxes = []
        actual_bboxes = []
        file_name = None
        page_size = None
        labels = []
        for line, bline, iline in zip(f, fb, fi):
            if line.startswith("-DOCSTART-") or line == "" or line == "\n":
                if words:
                    examples.append(
                        InputExample(
                            guid="{}-{}".format(mode, guid_index),
                            words=words,
                            labels=labels,
                            boxes=boxes,
                            actual_bboxes=actual_bboxes,
                            file_name=file_name,
                            page_size=page_size,
                        )
                    )
                    guid_index += 1
                    words = []
                    boxes = []
                    actual_bboxes = []
                    file_name = None
                    page_size = None
                    labels = []
            else:
                splits = line.split("\t")
                bsplits = bline.split("\t")
                isplits = iline.split("\t")
                assert len(splits) == 2
                assert len(bsplits) == 2
                assert len(isplits) == 4
                assert splits[0] == bsplits[0]
                words.append(splits[0])
                if len(splits) > 1:
                    labels.append(splits[-1].replace("\n", ""))
                    box = bsplits[-1].replace("\n", "")
                    box = [int(b) for b in box.split()]
                    boxes.append(box)
                    actual_bbox = [int(b) for b in isplits[1].split()]
                    actual_bboxes.append(actual_bbox)
                    page_size = [int(i) for i in isplits[2].split()]
                    file_name = isplits[3].strip()
                else:
                    # Examples could have no label for mode = "test"
                    labels.append("O")
        if words:
            examples.append(
                InputExample(
                    guid=("%s-%d" % (mode, guid_index)),
                    words=words,
                    labels=labels,
                    boxes=boxes,
                    actual_bboxes=actual_bboxes,
                    file_name=file_name,
                    page_size=page_size,
                )
            )
    return examples


def convert_examples_to_features(
    examples,
    label_list,
    max_seq_length,
    tokenizer,
    cls_token_at_end=False,
    cls_token="[CLS]",
    cls_token_segment_id=1,
    sep_token="[SEP]",
    sep_token_extra=False,
    pad_on_left=False,
    pad_token=0,
    cls_token_box=[0, 0, 0, 0],
    sep_token_box=[1000, 1000, 1000, 1000],
    pad_token_box=[0, 0, 0, 0],
    pad_token_segment_id=0,
    pad_token_label_id=-1,
    sequence_a_segment_id=0,
    mask_padding_with_zero=True,
):
    """Loads a data file into a list of `InputBatch`s
    `cls_token_at_end` define the location of the CLS token:
        - False (Default, BERT/XLM pattern): [CLS] + A + [SEP] + B + [SEP]
        - True (XLNet/GPT pattern): A + [SEP] + B + [SEP] + [CLS]
    `cls_token_segment_id` define the segment id associated to the CLS token (0 for BERT, 2 for XLNet)
    """

    label_map = {label: i for i, label in enumerate(label_list)}

    features = []
    for ex_index, example in enumerate(examples):
        file_name = example.file_name
        page_size = example.page_size
        width, height = page_size
        if ex_index % 10000 == 0:
            logger.info("Writing example %d of %d", ex_index, len(examples))

        tokens = []
        token_boxes = []
        actual_bboxes = []
        label_ids = []
        for word, label, box, actual_bbox in zip(
            example.words, example.labels, example.boxes, example.actual_bboxes
        ):
            word_tokens = tokenizer.tokenize(word)
            tokens.extend(word_tokens)
            token_boxes.extend([box] * len(word_tokens))
            actual_bboxes.extend([actual_bbox] * len(word_tokens))
            # Use the real label id for the first token of the word, and padding ids for the remaining tokens
            label_ids.extend(
                [label_map[label]] + [pad_token_label_id] * (len(word_tokens) - 1)
            )

        # Account for [CLS] and [SEP] with "- 2" and with "- 3" for RoBERTa.
        special_tokens_count = 3 if sep_token_extra else 2
        if len(tokens) > max_seq_length - special_tokens_count:
            tokens = tokens[: (max_seq_length - special_tokens_count)]
            token_boxes = token_boxes[: (max_seq_length - special_tokens_count)]
            actual_bboxes = actual_bboxes[: (max_seq_length - special_tokens_count)]
            label_ids = label_ids[: (max_seq_length - special_tokens_count)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids:   0   0  0    0    0     0       0   0   1  1  1  1   1   1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids:   0   0   0   0  0     0   0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambiguously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens += [sep_token]
        token_boxes += [sep_token_box]
        actual_bboxes += [[0, 0, width, height]]
        label_ids += [pad_token_label_id]
        if sep_token_extra:
            # roberta uses an extra separator b/w pairs of sentences
            tokens += [sep_token]
            token_boxes += [sep_token_box]
            actual_bboxes += [[0, 0, width, height]]
            label_ids += [pad_token_label_id]
        segment_ids = [sequence_a_segment_id] * len(tokens)

        if cls_token_at_end:
            tokens += [cls_token]
            token_boxes += [cls_token_box]
            actual_bboxes += [[0, 0, width, height]]
            label_ids += [pad_token_label_id]
            segment_ids += [cls_token_segment_id]
        else:
            tokens = [cls_token] + tokens
            token_boxes = [cls_token_box] + token_boxes
            actual_bboxes = [[0, 0, width, height]] + actual_bboxes
            label_ids = [pad_token_label_id] + label_ids
            segment_ids = [cls_token_segment_id] + segment_ids

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1 if mask_padding_with_zero else 0] * len(input_ids)

        # Zero-pad up to the sequence length.
        padding_length = max_seq_length - len(input_ids)
        if pad_on_left:
            input_ids = ([pad_token] * padding_length) + input_ids
            input_mask = (
                [0 if mask_padding_with_zero else 1] * padding_length
            ) + input_mask
            segment_ids = ([pad_token_segment_id] * padding_length) + segment_ids
            label_ids = ([pad_token_label_id] * padding_length) + label_ids
            token_boxes = ([pad_token_box] * padding_length) + token_boxes
        else:
            input_ids += [pad_token] * padding_length
            input_mask += [0 if mask_padding_with_zero else 1] * padding_length
            segment_ids += [pad_token_segment_id] * padding_length
            label_ids += [pad_token_label_id] * padding_length
            token_boxes += [pad_token_box] * padding_length

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length
        assert len(label_ids) == max_seq_length
        assert len(token_boxes) == max_seq_length

        if False:  # ex_index < 5:
            logger.info("*** Example ***")
            logger.info("guid: %s", example.guid)
            logger.info("tokens: %s", " ".join([str(x) for x in tokens]))
            logger.info("input_ids: %s", " ".join([str(x) for x in input_ids]))
            logger.info("input_mask: %s", " ".join([str(x) for x in input_mask]))
            logger.info("segment_ids: %s", " ".join([str(x) for x in segment_ids]))
            logger.info("label_ids: %s", " ".join([str(x) for x in label_ids]))
            logger.info("boxes: %s", " ".join([str(x) for x in token_boxes]))
            logger.info("actual_bboxes: %s", " ".join([str(x) for x in actual_bboxes]))

        features.append(
            InputFeatures(
                input_ids=input_ids,
                input_mask=input_mask,
                segment_ids=segment_ids,
                label_ids=label_ids,
                boxes=token_boxes,
                actual_bboxes=actual_bboxes,
                file_name=file_name,
                page_size=page_size,
            )
        )
    return features


class MyLayoutLMForTokenClassification(LayoutLMForTokenClassification):
    def __init__(self, config, label_weights):
        super().__init__(config)
        self.label_weights = label_weights

    # Just copy the code ... not very robust
    def forward(
        self,
        input_ids: Optional[torch.LongTensor] = None,
        bbox: Optional[torch.LongTensor] = None,
        attention_mask: Optional[torch.FloatTensor] = None,
        token_type_ids: Optional[torch.LongTensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        head_mask: Optional[torch.FloatTensor] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        labels: Optional[torch.LongTensor] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
    ) -> Union[Tuple, TokenClassifierOutput]:
        return_dict = (
            return_dict if return_dict is not None else self.config.use_return_dict
        )

        outputs = self.layoutlm(
            input_ids=input_ids,
            bbox=bbox,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        sequence_output = outputs[0]

        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)

        loss = None
        if labels is not None:
            loss_fct = CrossEntropyLoss(weight=self.label_weights)

            loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))

        if not return_dict:
            output = (logits,) + outputs[2:]
            return ((loss,) + output) if loss is not None else output

        return TokenClassifierOutput(
            loss=loss,
            logits=logits,
            hidden_states=outputs.hidden_states,
            attentions=outputs.attentions,
        )


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("-d", "--data-dir", default="data", type=Path)
parser.add_argument(
    "-m", "--model-name-or-path", default="microsoft/layoutlm-base-uncased"
)
parser.add_argument("--max-seq-length", default=512, type=int)
parser.add_argument("-t", "--model-type", default="layoutlm")
parser.add_argument("-n", "--nepoch", default=3, type=int)
parser.add_argument("-s", "--scores", default="layoutlmscores.csv", type=Path)
args = parser.parse_args()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
tokenizer = LayoutLMTokenizer.from_pretrained("microsoft/layoutlm-base-uncased")
# Use cross entropy ignore index as padding label id so that only real label ids contribute to the loss later
pad_token_label_id = CrossEntropyLoss().ignore_index

scores = {"test_macro_f1": []}
folds = sorted(args.data_dir.iterdir())
for folddir in folds:
    if not folddir.is_dir():
        continue
    if not folddir.name.startswith("fold"):
        continue
    fold = int(folddir.name[4:])
    logger.info("Training fold %d", fold)

    def get_labels(path):
        with open(path, "r") as f:
            labels = f.read().splitlines()
        if "O" not in labels:
            labels = ["O"] + labels
        return labels

    def count_labels(path):
        with open(path, "rt") as infh:
            return Counter(
                spam.split(maxsplit=2)[1] for spam in infh if not spam.isspace()
            )

    labels = get_labels(folddir / "labels.txt")
    # FIXME: This is really redundant
    label_counts = count_labels(folddir / "train.txt")
    num_labels = len(labels)
    # FIXME: This is so dumb! It does nothing a list wouldn't do!
    label_map = {i: label for i, label in enumerate(labels)}

    # We created our own dataset following the FUNSD conventions (see make_funsd_data.py)
    args.data_dir = folddir
    train_dataset = FunsdDataset(
        args, tokenizer, labels, pad_token_label_id, mode="train"
    )
    train_sampler = RandomSampler(train_dataset)
    train_dataloader = DataLoader(train_dataset, sampler=train_sampler, batch_size=2)

    eval_dataset = FunsdDataset(
        args, tokenizer, labels, pad_token_label_id, mode="test"
    )
    eval_sampler = SequentialSampler(eval_dataset)
    eval_dataloader = DataLoader(eval_dataset, sampler=eval_sampler, batch_size=2)

    model = MyLayoutLMForTokenClassification.from_pretrained(
        "microsoft/layoutlm-base-uncased",
        num_labels=num_labels,
        label_weights=torch.FloatTensor([1.0 / label_counts[x] for x in labels]).to(device),
    )
    model.to(device)

    # optimizer = Adafactor(model.parameters(), scale_parameter=True, relative_step=True, warmup_init=True, lr=None)
    optimizer = Adafactor(
        model.parameters(),
        lr=5e-5,
        relative_step=False,
        scale_parameter=False,
        warmup_init=False,
    )

    global_step = 0
    t_total = len(train_dataloader) * args.nepoch  # total number of training steps

    # put the model in training mode
    model.train()
    for _epoch in range(args.nepoch):
        for batch in tqdm(train_dataloader, desc="Training"):
            input_ids = batch[0].to(device)
            bbox = batch[4].to(device)
            attention_mask = batch[1].to(device)
            token_type_ids = batch[2].to(device)
            labels = batch[3].to(device)

            # forward pass
            outputs = model(
                input_ids=input_ids,
                bbox=bbox,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
                labels=labels,
            )
            loss = outputs.loss

            # print loss every 50 steps
            if global_step % 50 == 0:
                logger.info(f"Loss after {global_step} steps: {loss.item()}")

            # backward pass to get the gradients
            loss.backward()

            # print("Gradients on classification head:")
            # print(model.classifier.weight.grad[6,:].sum())

            # update
            optimizer.step()
            optimizer.zero_grad()
            global_step += 1

    eval_loss = 0.0
    nb_eval_steps = 0
    preds = None
    out_label_ids = None

    # put model in evaluation mode
    model.eval()
    for batch in tqdm(eval_dataloader, desc="Evaluating"):
        with torch.no_grad():
            input_ids = batch[0].to(device)
            bbox = batch[4].to(device)
            attention_mask = batch[1].to(device)
            token_type_ids = batch[2].to(device)
            labels = batch[3].to(device)

            # forward pass
            outputs = model(
                input_ids=input_ids,
                bbox=bbox,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
                labels=labels,
            )
            # get the loss and logits
            tmp_eval_loss = outputs.loss
            logits = outputs.logits

            eval_loss += tmp_eval_loss.item()
            nb_eval_steps += 1

            # compute the predictions
            if preds is None:
                preds = logits.detach().cpu().numpy()
                out_label_ids = labels.detach().cpu().numpy()
            else:
                preds = np.append(preds, logits.detach().cpu().numpy(), axis=0)
                out_label_ids = np.append(
                    out_label_ids, labels.detach().cpu().numpy(), axis=0
                )

    # compute average evaluation loss
    eval_loss = eval_loss / nb_eval_steps
    preds = np.argmax(preds, axis=2)

    y_true = [[] for _ in range(out_label_ids.shape[0])]
    y_pred = [[] for _ in range(out_label_ids.shape[0])]

    for i in range(out_label_ids.shape[0]):
        for j in range(out_label_ids.shape[1]):
            if out_label_ids[i, j] != pad_token_label_id:
                y_true[i].append(label_map[out_label_ids[i][j]])
                y_pred[i].append(label_map[preds[i][j]])
    eval_labels = [
        "B-Article",
        "B-Chapitre",
        "B-Section",
        "B-SousSection",
        "B-Titre",
    ]

    macro_f1 = metrics.flat_f1_score(
        y_true, y_pred, labels=eval_labels, average="macro", zero_division=0.0
    )
    scores["test_macro_f1"].append(macro_f1)
    logger.info("fold %d ALL %f", fold, macro_f1)
    for name in eval_labels:
        label_f1 = metrics.flat_f1_score(
            y_true, y_pred, labels=[name], average="micro", zero_division=0.0
        )
        scores.setdefault(name, []).append(label_f1)
        logger.info("fold %d %s %f", fold, name, label_f1)

with open(args.scores, "wt") as outfh:
    fieldnames = [
        "Label",
        "Average",
        *range(1, len(scores["test_macro_f1"]) + 1),
    ]
    writer = csv.DictWriter(outfh, fieldnames=fieldnames)
    writer.writeheader()

    def makerow(name, scores):
        row = {"Label": name, "Average": np.mean(scores)}
        for idx, score in enumerate(scores):
            row[idx + 1] = score
        return row

    row = makerow("ALL", scores["test_macro_f1"])
    writer.writerow(row)
    logger.info("average ALL %f", row["Average"])
    for name in eval_labels:
        row = makerow(name, scores[name])
        writer.writerow(row)
        logger.info("average %s %f", row["Label"], row["Average"])

model.save_pretrained("layoutlm-alexi")