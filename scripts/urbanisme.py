"""Extraire la structure et le contenu d'un règlement d'urbanisme en
format PDF.
"""

import argparse
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

import pdfplumber
import tqdm
from alexi.types import (Ancrage, Annexe, Article, Chapitre, Dates, Reglement,
                         Section, SousSection)


def make_argparse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text", help="Extraire le texte seulement", action="store_true"
    )
    parser.add_argument("pdf", help="Fichier PDF", type=Path)
    return parser


def extract_title(pages: List[str]) -> Tuple[Optional[str], Optional[str], str]:
    numero, objet = None, None
    for page in pages:
        m = re.search(
            r"(?i:règlement)(?:\s+(?:de|d'|sur))?\s+(.*)\s+(?i:numéro\s+(\S+))", page
        )
        if m:
            objet = m.group(1)
            numero = m.group(2)
            titre = re.sub(r"\s+", " ", m.group(0))
            break
    return numero, objet, titre


def extract_dates(pages: List[str]) -> Dates:
    """Extraire les dates d'avis de motion, adoption, et entrée en vigueur
    d'un texte règlement."""
    dates = {}
    for i, page in enumerate(pages):
        m = re.search(r"^ENTRÉE EN VIGUEUR", page, re.MULTILINE)
        if not m:
            continue
        startpos = m.start(0)
        m = re.search(r"avis de motion (.*)$", page, re.MULTILINE | re.IGNORECASE)
        if m:
            dates["avis"] = m.group(1)
        m = re.search(r"adoption (.*)$", page, re.MULTILINE | re.IGNORECASE)
        if m:
            dates["adoption"] = m.group(1)
        m = re.search(r"entrée en vigueur (.*)$", page, re.MULTILINE | re.IGNORECASE)
        if m:
            dates["entree"] = m.group(1)
        # Si on est rendu ici, on les a eues pour de vrai, alors enlever
        # le texte de la page (FIXME: means the last article has bogus pages)
        if "adoption" in dates and "entree" in dates:
            pages[i] = page[:startpos]
            break
    return Dates(**dates)


def extract_text_from_pdf(pdf: Path) -> List[str]:
    """Extraire les pages d'un PDF."""
    pages = []
    with pdfplumber.open(pdf) as doc:
        for page in tqdm.tqdm(doc.pages):
            # Enlever en-tete et en-pied
            page = page.crop((0, 72, page.width, page.height - 72))
            texte = page.extract_text()
            pages.append(texte)
    return pages


class Extracteur:
    fichier: str = "INCONNU"
    numero: str = "INCONNU"
    objet: Optional[str]
    titre: str = "INCONNU"
    dates: Dates
    chapitre: Optional[Chapitre] = None
    section: Optional[Section] = None
    sous_section: Optional[SousSection] = None
    annexe: Optional[Annexe] = None
    article: Optional[Article] = None
    artidx: int = -1
    pageidx: int = -1

    def __init__(self, fichier: Optional[str] = None):
        if fichier is not None:
            self.fichier = fichier
        self.pages: List[str] = []
        self.chapitres: List[Chapitre] = []
        self.articles: List[Article] = []
        self.annexes: List[Annexe] = []

    def close_annexe(self):
        """Clore la derniere annexe (et chapitre, et section, etc)"""
        if self.annexe:
            self.annexe.pages = (self.annexe.pages[0], self.pageidx)
        self.annexe = None
        self.close_chapitre()

    def close_chapitre(self):
        """Clore le dernier chapitre (et section, etc)"""
        if self.chapitre:
            self.chapitre.pages = (self.chapitre.pages[0], self.pageidx)
            self.chapitre.articles = (self.chapitre.articles[0], len(self.articles))
        self.chapitre = None
        self.close_section()

    def close_section(self):
        """Clore la derniere section (et sous-section, etc)"""
        if self.section:
            self.section.pages = (self.section.pages[0], self.pageidx)
            self.section.articles = (self.section.articles[0], len(self.articles))
        self.section = None
        self.close_sous_section()

    def close_sous_section(self):
        """Clore la derniere sous-section"""
        if self.sous_section:
            self.sous_section.pages = (self.sous_section.pages[0], self.pageidx)
            self.sous_section.articles = (
                self.sous_section.articles[0],
                len(self.articles),
            )
        self.sous_section = None
        self.close_article()

    def close_article(self):
        """Clore le dernier article"""
        if self.article:
            self.article.pages = (self.article.pages[0], self.pageidx)
        self.article = None

    def extract_chapitre(self) -> Optional[Chapitre]:
        texte = self.pages[self.pageidx]
        m = re.search(r"CHAPITRE\s+(\d+)\s+([^\.\d]+)(\d+)?$", texte)
        if m is None:
            return None
        numero = m.group(1)
        titre = re.sub(r"\s+", " ", m.group(2).strip())
        chapitre = Chapitre(
            numero=numero,
            titre=titre,
            pages=(self.pageidx, -1),
            articles=(len(self.articles), -1),
        )
        self.close_chapitre()
        self.chapitre = chapitre
        self.chapitres.append(self.chapitre)
        return chapitre

    def extract_annexe(self) -> Optional[Annexe]:
        texte = self.pages[self.pageidx]
        m = re.search(r"ANNEXE\s+(\S+)(?: –)?\s+([^\.\d]+)(\d+)?$", texte)
        if m is None:
            return None
        numero = m.group(1)
        titre = re.sub(r"\s+", " ", m.group(2).strip())
        annexe = Annexe(
            numero=numero,
            titre=titre,
            pages=(self.pageidx, -1),
        )
        # Mettre à jour les indices de pages de la derniere annexe, chapitre, section, etc
        self.close_annexe()
        self.annexe = annexe
        self.annexes.append(self.annexe)
        return annexe

    def extract_section(self, ligne: str) -> Optional[Section]:
        m = re.match(r"SECTION\s+(\d+)\s+(.*)", ligne)
        if m is None:
            return None
        sec = m.group(1)
        titre = re.sub(r"\s+", " ", m.group(2).strip())
        section = Section(
            numero=sec,
            titre=titre,
            pages=(self.pageidx, -1),
            articles=(len(self.articles), -1),
        )
        self.close_section()
        if self.chapitre:
            self.chapitre.sections.append(section)
        self.section = section
        return section

    def extract_sous_section(self, ligne: str) -> Optional[SousSection]:
        m = re.match(r"SOUS-SECTION\s+\d+\.(\d+)\s+(.*)", ligne)
        if m is None:
            return None
        sec = m.group(1)
        titre = re.sub(r"\s+", " ", m.group(2).strip())
        sous_section = SousSection(
            numero=sec,
            titre=titre,
            pages=(self.pageidx, -1),
            articles=(len(self.articles), -1),
        )
        self.close_sous_section()
        if self.section:
            self.section.sous_sections.append(sous_section)
        self.sous_section = sous_section
        return sous_section

    def extract_non_numerotee(self, ligne: str) -> Optional[SousSection]:
        return None

    def continue_section_title(self, ligne: str) -> Optional[Ancrage]:
        """Détecte la continuation d'un titre de section, retourner la section
        en question."""
        # Devrait pas y avoir un article déjà commencé
        if self.article is not None:
            return None
        # Exception pour les descriptions de zones
        if ligne == "INTENTION":
            return None
        # Devrait y avoir une (sous-)section avec pas d'articles
        if self.section and self.section.articles == len(self.articles):
            self.section.titre += f" {ligne}"
            return self.section
        if self.sous_section and self.sous_section.articles == len(self.articles):
            self.sous_section.titre += f" {ligne}"
            return self.sous_section
        return None

    def new_article(self, num: int, titre: str) -> Article:
        article = Article(
            chapitre=len(self.chapitres) - 1,
            section=(len(self.chapitre.sections) - 1 if self.chapitre else -1),
            sous_section=(len(self.section.sous_sections) - 1 if self.section else -1),
            numero=num,
            titre=titre,
            pages=(self.pageidx, -1),
            alineas=[],
        )
        self.close_article()
        self.articles.append(article)
        self.article = article
        return article

    def extract_article(self, ligne: str) -> Optional[Article]:
        m = re.match(r"(\d+)\.\s+(.*)", ligne)
        if m is None:
            # On le reconnaît pas comme un article, c'est peut-être un
            # sous-titre, on regardera ça plus tard
            return None
        num = int(m.group(1))
        if num <= self.artidx:
            # C'est plutôt une énumération quelconque, traiter comme un alinéa
            return None
        self.artidx = num
        return self.new_article(num, m.group(2))

    def extract_text(self, pages: Optional[List[str]] = None) -> Reglement:
        """Extraire la structure d'un règlement d'urbanisme du texte des pages."""
        if pages is not None:
            self.pages = pages
        numero, objet, self.titre = extract_title(self.pages)
        if numero is not None:
            self.numero = numero
        if objet is not None:
            self.objet = objet
        self.dates = extract_dates(self.pages)

        # Passer les tables de contenus pour trouver le premier chapitre
        self.pageidx = 0
        while self.pageidx < len(self.pages):
            if self.extract_chapitre():
                # Passer à la prochaine page
                self.pageidx += 1
                break
            self.pageidx += 1

        # Continuer pour extraire les contenus
        while self.pageidx < len(self.pages):
            if self.extract_chapitre():
                # Passer à la prochaine page
                self.pageidx += 1
                continue
            if self.extract_annexe():
                # Passer à la prochaine page
                self.pageidx += 1
                continue

            # Il devrait y en avoir rendu ici
            assert self.chapitres

            texte = self.pages[self.pageidx]
            for line in texte.strip().split("\n"):
                # Les annexes se trouvent à la fin du document, elles
                # vont simplement ramasser tout le contenu...
                if self.annexe:
                    self.annexe.alineas.append(line)
                    continue
                # Détecter sections et sous-sections
                if self.extract_section(line):
                    continue
                if self.extract_sous_section(line):
                    continue
                # Trouver des articles
                if self.extract_article(line):
                    continue
                # Rendu ici on a affaire à plusieurs possibilités.
                # 1. C'est possiblement la continuation d'un titre de
                # section (voici le problème de traîter une ligne à la
                # fois)
                if self.continue_section_title(line):
                    continue
                # 2. C'est possiblement une sous-section sans numéro
                # (souvent dans des descriptions de zones)
                if self.extract_non_numerotee(line):
                    continue
                # 3. C'est ... autre chose, on l'appelle un article
                # sans numéro.
                if self.article is None:
                    self.new_article(-1, line)
                    continue
                # 4. C'est un alinéa supplémentaire pour le dernier
                # article.  (FIXME: pas vraiment un alinéa, une ligne)
                if self.article:
                    self.article.alineas.append(line)
                # NOTE: à l'avenir on va faire de la vraie extraction
                # d'information mais il faudra faire des annotations
                # et entraîner un modèle, ainsi qu'avoir un modèle à
                # suivre, on va y arriver ;-)
            self.pageidx += 1
        # Clore toutes les annexes, sections, chapitres, etc
        self.close_annexe()
        self.close_article()
        return Reglement(
            fichier=self.fichier,
            titre=self.titre,
            numero=self.numero,
            objet=self.objet,
            dates=self.dates,
            chapitres=self.chapitres,
            articles=self.articles,
            annexes=self.annexes,
        )

    def __call__(self, pdf: Path, fichier: Optional[str] = None) -> Reglement:
        """Extraire la structure d'un règlement d'urbanisme d'un PDF."""
        self.fichier = pdf.name if fichier is None else fichier
        self.pages = extract_text_from_pdf(pdf)
        return self.extract_text()


def main():
    parser = make_argparse()
    args = parser.parse_args()
    ex = Extracteur()
    if args.text:
        print(json.dumps(extract_text_from_pdf(args.pdf), indent=2, ensure_ascii=False))
    else:
        print(ex(args.pdf).json(indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()