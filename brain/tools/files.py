import glob as glob_mod
import os
import shutil
from pathlib import Path

from . import tool


@tool("lire_fichier", "Lit le contenu d'un fichier texte. Utile pour les fichiers .txt, .py, .json, .csv, etc.")
def read_file(path: str, offset: int = 0, limit: int = 100) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Fichier introuvable : {path}"
    if not p.is_file():
        return f"Ce n'est pas un fichier : {path}"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            if offset > 0:
                for _ in range(offset):
                    f.readline()
            lines = []
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break
                lines.append(line.rstrip())
            content = "\n".join(lines)
            total = sum(1 for _ in open(p, "rb"))
            return f"{path} (lignes {offset+1}-{offset+len(lines)}/{total}):\n{content}"
    except Exception as e:
        return f"Erreur de lecture de {path} : {e}"


@tool("lire_image", "Affiche les métadonnées d'une image (taille, dimensions, type).", dangerous=False)
def read_image(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Fichier introuvable : {path}"
    try:
        size = p.stat().st_size
        ext = p.suffix.lower()
        from PIL import Image
        img = Image.open(p)
        w, h = img.size
        fmt = img.format or ext
        return f"📷 {p.name} | Format: {fmt} | Dimensions: {w}x{h} | Poids: {_fmt_size(size)}"
    except ImportError:
        import subprocess
        try:
            result = subprocess.run(["file", str(p)], capture_output=True, text=True)
            info = result.stdout.strip() or p.name
            return f"📷 {p.name} | {info} | Taille: {_fmt_size(size)}"
        except Exception:
            return f"📷 {p.name} | Taille: {_fmt_size(size)}"
    except Exception as e:
        return f"Erreur de lecture image : {e}"


@tool("lire_pdf", "Extrait le texte d'un fichier PDF.")
def read_pdf(path: str, max_pages: int = 5) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Fichier introuvable : {path}"
    try:
        import fitz
        doc = fitz.open(str(p))
        pages = min(len(doc), max_pages)
        parts = [f"📄 {p.name} — {len(doc)} pages"]
        for i in range(pages):
            text = doc[i].get_text().strip()
            if text:
                parts.append(f"--- Page {i+1} ---\n{text[:2000]}")
        doc.close()
        return "\n\n".join(parts) if len(parts) > 1 else f"📄 {p.name} — texte non extractible"
    except ImportError:
        return f"Bibliothèque PDF manquante. Installe PyMuPDF."
    except Exception as e:
        return f"Erreur de lecture PDF : {e}"


@tool("lire_docx", "Extrait le texte d'un fichier Word (.docx).")
def read_docx(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Fichier introuvable : {path}"
    try:
        import docx
        doc = docx.Document(str(p))
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        text = "\n".join(paragraphs)
        if not text:
            return f"📄 {p.name} — document vide"
        return f"📄 {p.name} ({len(paragraphs)} paragraphes):\n{text[:5000]}"
    except ImportError:
        return f"Bibliothèque docx manquante. Installe python-docx."
    except Exception as e:
        return f"Erreur de lecture DOCX : {e}"


@tool("lister_dossier", "Liste le contenu d'un dossier.")
def list_directory(path: str = ".") -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Dossier introuvable : {path}"
    if not p.is_dir():
        return f"Ce n'est pas un dossier : {path}"
    try:
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        total = len(entries)
        MAX = 50
        lines = [f"📁 {p}/ ({total} entrées)"]
        for entry in entries[:MAX]:
            if entry.is_dir():
                lines.append(f"  📁 {entry.name}/")
            else:
                size = _fmt_size(entry.stat().st_size)
                lines.append(f"  📄 {entry.name} ({size})")
        if total > MAX:
            lines.append(f"  ... et {total - MAX} autres entrées")
        return "\n".join(lines)
    except Exception as e:
        return f"Erreur de lecture du dossier : {e}"


@tool("rechercher_fichiers", "Cherche des fichiers par motif (glob). Ex: « *.txt », « **/*.py »")
def search_files(pattern: str, path: str = ".") -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Dossier introuvable : {path}"
    try:
        matches = sorted(glob_mod.glob(pattern, root_path=p, recursive=True))
        total = len(matches)
        MAX = 30
        if not matches:
            return f"Aucun fichier trouvé pour « {pattern} » dans {p}"
        lines = [f"🔍 {total} résultat(s) pour « {pattern} » dans {p}"]
        for m in matches[:MAX]:
            fp = p / m
            size = _fmt_size(fp.stat().st_size) if fp.is_file() else ""
            lines.append(f"  {m} {size}")
        if total > MAX:
            lines.append(f"  ... et {total - MAX} autres")
        return "\n".join(lines)
    except Exception as e:
        return f"Erreur de recherche : {e}"


@tool("chercher_texte", "Cherche un texte dans les fichiers (grep).")
def grep_files(pattern: str, path: str = ".") -> str:
    import re
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Dossier introuvable : {path}"
    try:
        matches = []
        for fpath in p.rglob("*"):
            if fpath.is_file() and fpath.stat().st_size < 1024 * 1024:
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            rel = fpath.relative_to(p)
                            matches.append(f"{rel}:{i}: {line.strip()[:150]}")
                            if len(matches) >= 20:
                                break
                except Exception:
                    pass
                if len(matches) >= 20:
                    break
        if not matches:
            return f"Aucun résultat pour « {pattern} » dans {p}"
        return f"🔍 {len(matches)} résultat(s) pour « {pattern} »:\n" + "\n".join(matches)
    except Exception as e:
        return f"Erreur de recherche : {e}"


@tool("copier_fichier", "Copie un fichier ou dossier.", dangerous=True)
def copy_file(src: str, dst: str) -> str:
    src_p = Path(src).expanduser().resolve()
    dst_p = Path(dst).expanduser().resolve()
    if not src_p.exists():
        return f"Source introuvable : {src}"
    try:
        if src_p.is_dir():
            shutil.copytree(src_p, dst_p)
            return f"📋 Dossier copié : {src} → {dst}"
        shutil.copy2(src_p, dst_p)
        return f"📋 Fichier copié : {src} → {dst}"
    except Exception as e:
        return f"Erreur de copie : {e}"


@tool("deplacer_fichier", "Déplace ou renomme un fichier ou dossier.", dangerous=True)
def move_file(src: str, dst: str) -> str:
    src_p = Path(src).expanduser().resolve()
    dst_p = Path(dst).expanduser().resolve()
    if not src_p.exists():
        return f"Source introuvable : {src}"
    try:
        shutil.move(str(src_p), str(dst_p))
        return f"📦 Déplacé : {src} → {dst}"
    except Exception as e:
        return f"Erreur de déplacement : {e}"


@tool("supprimer_fichier", "Supprime définitivement un fichier ou dossier vide.", dangerous=True)
def delete_file(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Introuvable : {path}"
    try:
        if p.is_dir():
            p.rmdir()
            return f"🗑 Dossier supprimé : {path}"
        p.unlink()
        return f"🗑 Fichier supprimé : {path}"
    except OSError as e:
        return f"Erreur de suppression : {e}. Le dossier doit être vide."


@tool("creer_dossier", "Crée un dossier (et ses parents si nécessaire).")
def create_directory(path: str) -> str:
    p = Path(path).expanduser().resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"📁 Dossier créé : {path}"
    except Exception as e:
        return f"Erreur de création : {e}"


def _fmt_size(size: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} To"
