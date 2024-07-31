"""Tester un RNN"""

import argparse
import json
import logging
from pathlib import Path

import torch
from sklearn_crfsuite import metrics
from torch.utils.data import DataLoader
from tokenizers import Tokenizer

from alexi.segment import (
    load,
    load_rnn_data,
    pad_collate_fn_predict,
    RNN,
    RNNCRF,
    bio_transitions,
    retokenize,
)

from allennlp_light.modules.conditional_random_field import ConditionalRandomField


def make_argparse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-m", "--model", help="Fichier modele", default="rnn.pt", type=Path
    )
    parser.add_argument(
        "--device", default="cuda:0", help="Device pour rouler la prediction"
    )
    parser.add_argument(
        "--decode", action="store_true", help="Appliquer l'algorithme Viterbi"
    )
    parser.add_argument(
        "--all-labels", action="store_true", help="Evaluater toutes les classes"
    )
    parser.add_argument(
        "-t", "--tokenize", action="store_true", help="Tokeniser les mots"
    )
    parser.add_argument("csvs", nargs="+", help="Fichiers CSV de test", type=Path)
    return parser


def main():
    parser = make_argparse()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    with open(args.model.with_suffix(".json"), "rt") as infh:
        config = json.load(infh)
        id2label = config["id2label"]
        feat2id = config["feat2id"]
    tokenizer = None
    words = load(args.csvs)
    if args.tokenize:
        tokenizer = Tokenizer.from_pretrained("camembert-base")
        words = retokenize(words, tokenizer, drop=True)
    all_data = load_rnn_data(
        words, feat2id, id2label, config["features"], config["labels"]
    )
    ordering, sorted_test_data = zip(
        *sorted(enumerate(all_data), reverse=True, key=lambda x: len(x[1][0]))
    )
    test_loader = DataLoader(
        sorted_test_data,
        batch_size=32,
        collate_fn=pad_collate_fn_predict,
    )
    device = torch.device(args.device)
    if "crf" in args.model.name:
        model = RNNCRF(**config)
    else:
        model = RNN(**config)
    if args.decode:
        crf = ConditionalRandomField(
            num_tags=len(id2label),
            constraints=bio_transitions(id2label),
            include_start_end_transitions=False,
        )

    model.load_state_dict(torch.load(args.model))
    model.eval()
    model.to(device)
    predictions = []
    lengths = [len(tokens) for tokens, _ in sorted_test_data]
    for batch in test_loader:
        if "crf" in args.model.name:
            _logits, labels, _mask = model(*(t.to(device) for t in batch))
            predictions.extend(labels)
        else:
            features, vectors, mask = batch
            out = model(*(t.to(device) for t in (features, vectors, mask)))
            out = out.transpose(1, -1).cpu()  # WTF
            if args.decode:
                tags = crf.viterbi_tags(out, mask)
                predictions.extend(sequence for sequence, _ in tags)
            else:
                for length, row in zip(lengths, out.argmax(-1).cpu()):
                    predictions.append(row[:length])
                del lengths[: len(out)]
    y_pred = [[id2label[x] for x in page] for page in predictions]
    y_true = [[id2label[x] for x in page] for _, page in sorted_test_data]
    if args.all_labels:
        eval_labels = sorted(id2label)
    else:
        eval_labels = sorted(
            ["O", *(c for c in id2label if c.startswith("B-"))],
            key=lambda name: (name[1:], name[0]),
        )
    report = metrics.flat_classification_report(
        y_true, y_pred, labels=eval_labels, zero_division=0.0
    )
    print(report)


if __name__ == "__main__":
    main()