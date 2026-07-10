import argparse
import json
import math
import os
import re
import sys
import zipfile
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
INDEX_VERSION = 1


@dataclass
class DocumentChunk:
    chunk_id: str
    source: str
    text: str


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        data = data.strip()
        if data:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(self.parts)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def read_html_file(path: Path) -> str:
    parser = TextExtractor()
    parser.feed(read_text_file(path))
    return parser.text()


def read_docx_file(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")

    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def read_pptx_file(path: Path) -> str:
    parts: list[str] = []
    namespace = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
        for slide_name in slide_names:
            root = ElementTree.fromstring(archive.read(slide_name))
            slide_text = [node.text for node in root.findall(".//a:t", namespace) if node.text]
            if slide_text:
                parts.append("\n".join(slide_text))
    return "\n\n".join(parts)


def read_xlsx_file(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_xlsx_shared_strings(archive)
        sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        sheets: list[str] = []
        for sheet_name in sheet_names:
            sheets.append(read_xlsx_sheet(archive, sheet_name, shared_strings))
    return "\n\n".join(sheet for sheet in sheets if sheet.strip())


def read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall(".//main:si", namespace):
        strings.append("".join(node.text or "" for node in item.findall(".//main:t", namespace)))
    return strings


def read_xlsx_sheet(archive: zipfile.ZipFile, sheet_name: str, shared_strings: list[str]) -> str:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ElementTree.fromstring(archive.read(sheet_name))
    rows: list[str] = []
    for row in root.findall(".//main:row", namespace):
        values: list[str] = []
        for cell in row.findall("main:c", namespace):
            value_node = cell.find("main:v", namespace)
            if value_node is None or value_node.text is None:
                continue
            value = value_node.text
            if cell.attrib.get("t") == "s":
                index = int(value)
                value = shared_strings[index] if index < len(shared_strings) else value
            values.append(value)
        if values:
            rows.append(" | ".join(values))
    return "\n".join(rows)


def read_ipynb_file(path: Path) -> str:
    notebook = json.loads(read_text_file(path))
    parts: list[str] = []
    for cell in notebook.get("cells", []):
        source = cell.get("source", [])
        if isinstance(source, list):
            source_text = "".join(source)
        else:
            source_text = str(source)
        if source_text.strip():
            parts.append(source_text)
    return "\n\n".join(parts)


def read_pdf_file(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support needs pypdf. Install it with: python -m pip install pypdf") from exc

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def read_supported_file(path: Path) -> str | None:
    suffix = path.suffix.lower()
    text_extensions = {
        ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h",
        ".hpp", ".cs", ".go", ".rs", ".sql", ".json", ".yaml", ".yml", ".csv", ".log",
    }

    if suffix in text_extensions:
        return read_text_file(path)
    if suffix in {".html", ".htm"}:
        return read_html_file(path)
    if suffix == ".docx":
        return read_docx_file(path)
    if suffix == ".pptx":
        return read_pptx_file(path)
    if suffix == ".xlsx":
        return read_xlsx_file(path)
    if suffix == ".ipynb":
        return read_ipynb_file(path)
    if suffix == ".pdf":
        return read_pdf_file(path)
    return None


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_+#.%-]+|[\u4e00-\u9fff]", text.lower())
    return [token for token in tokens if token.strip()]


def split_chunks(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind("\u3002", start, end), text.rfind(".", start, end), text.rfind("\n", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = max(0, end - overlap)

    return chunks


def load_documents(data_dir: Path) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    skipped: list[str] = []

    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue

        rel = str(path.relative_to(data_dir))
        try:
            text = read_supported_file(path)
        except Exception as exc:
            skipped.append(f"{rel}: {exc}")
            continue

        if text is None:
            skipped.append(f"{rel}: unsupported file type")
            continue

        for idx, chunk in enumerate(split_chunks(text)):
            chunks.append(DocumentChunk(chunk_id=f"{rel}#{idx}", source=rel, text=chunk))

    if skipped:
        print("Skipped files:")
        for item in skipped:
            print(f"- {item}")

    return chunks


def build_index(data_dir: Path, index_path: Path) -> None:
    chunks = load_documents(data_dir)
    if not chunks:
        raise SystemExit(f"No readable learning files found in {data_dir}")

    doc_freq: Counter[str] = Counter()
    chunk_terms: list[Counter[str]] = []

    for chunk in chunks:
        terms = Counter(tokenize(chunk.text))
        chunk_terms.append(terms)
        doc_freq.update(terms.keys())

    payload = {
        "version": INDEX_VERSION,
        "data_dir": str(data_dir),
        "chunks": [chunk.__dict__ for chunk in chunks],
        "doc_freq": dict(doc_freq),
        "chunk_terms": [dict(terms) for terms in chunk_terms],
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Indexed {len(chunks)} chunks from {data_dir} -> {index_path}")


def load_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        raise SystemExit(f"Index not found: {index_path}. Run: python deepseek_learning_ai.py index")

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if payload.get("version") != INDEX_VERSION:
        raise SystemExit("Index version mismatch. Rebuild with: python deepseek_learning_ai.py index")
    return payload


def ensure_index(data_dir: Path, index_path: Path) -> None:
    if index_path.exists():
        return
    print(f"Index not found. Building it from {data_dir} first...")
    build_index(data_dir, index_path)


def bm25_search(payload: dict[str, Any], query: str, top_k: int = 5) -> list[DocumentChunk]:
    chunks = [DocumentChunk(**chunk) for chunk in payload["chunks"]]
    chunk_terms = [Counter(terms) for terms in payload["chunk_terms"]]
    doc_freq = payload["doc_freq"]
    query_terms = tokenize(query)
    total_docs = len(chunks)
    avg_len = sum(sum(terms.values()) for terms in chunk_terms) / max(total_docs, 1)

    scores: defaultdict[int, float] = defaultdict(float)
    k1 = 1.5
    b = 0.75

    for term in query_terms:
        df = doc_freq.get(term, 0)
        if df == 0:
            continue
        idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))

        for idx, terms in enumerate(chunk_terms):
            freq = terms.get(term, 0)
            if freq == 0:
                continue
            doc_len = sum(terms.values())
            denom = freq + k1 * (1 - b + b * doc_len / max(avg_len, 1))
            scores[idx] += idf * freq * (k1 + 1) / denom

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [chunks[idx] for idx, _score in ranked[:top_k]]


def deepseek_chat(api_key: str, messages: list[dict[str, str]], model: str, temperature: float = 0.2) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        DEEPSEEK_CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise SystemExit(f"DeepSeek API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error when calling DeepSeek: {exc}") from exc

    return payload["choices"][0]["message"]["content"]


def build_messages(question: str, contexts: list[DocumentChunk]) -> list[dict[str, str]]:
    context_text = "\n\n".join(
        f"[Source: {chunk.source}]\n{chunk.text}" for chunk in contexts
    )

    system_prompt = (
        "You are a personalized AI tutor for computer science and finance. "
        "Use the provided local notes as the primary source. "
        "If the notes are insufficient, say what is missing and give a cautious general explanation. "
        "For finance topics, provide education only, not personalized investment advice. "
        "Answer in Chinese unless the user asks otherwise."
    )
    user_prompt = (
        f"Local notes:\n{context_text or 'No relevant local notes found.'}\n\n"
        f"Question:\n{question}\n\n"
        "Please explain step by step, include examples when useful, and list which local sources you used."
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def ask_question(index_path: Path, question: str, top_k: int, model: str) -> None:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("Missing DEEPSEEK_API_KEY. Set it first, then run again.")

    payload = load_index(index_path)
    contexts = bm25_search(payload, question, top_k=top_k)
    answer = deepseek_chat(api_key, build_messages(question, contexts), model=model)

    print("\nAnswer:\n")
    print(answer)
    print("\nLocal sources sent to DeepSeek:")
    for chunk in contexts:
        print(f"- {chunk.source}")


def interactive_chat(index_path: Path, top_k: int, model: str) -> None:
    print("Personal learning AI. Type 'exit' to quit.")
    while True:
        question = input("\nYou> ").strip()
        if question.lower() in {"exit", "quit", "q"}:
            break
        if not question:
            continue
        ask_question(index_path, question, top_k, model)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Local-data, cloud-inference learning AI using DeepSeek.")
    parser.add_argument("--data-dir", default="learning_data", help="Directory containing local .txt/.md notes.")
    parser.add_argument("--index", default=".learning_index/index.json", help="Local index file path.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of local chunks to send to DeepSeek.")
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL), help="DeepSeek model name.")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("index", help="Build or rebuild the local search index.")

    ask_parser = subparsers.add_parser("ask", help="Ask one question.")
    ask_parser.add_argument("question", help="Question to ask.")

    subparsers.add_parser("chat", help="Start an interactive chat.")

    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    index_path = Path(args.index)
    command = args.command or "chat"

    if command == "index":
        build_index(data_dir, index_path)
    elif command == "ask":
        ensure_index(data_dir, index_path)
        ask_question(index_path, args.question, args.top_k, args.model)
    elif command == "chat":
        ensure_index(data_dir, index_path)
        interactive_chat(index_path, args.top_k, args.model)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
