# Local Data + DeepSeek Cloud Inference Learning AI

This project keeps your notes on the local machine, builds a local search index, retrieves
the most relevant snippets locally, and sends only those snippets plus your question to
DeepSeek for answer generation.

## Privacy boundary

- Local: your `.md` and `.txt` files, search index, and retrieval ranking.
- Cloud: your question and the selected snippets are sent to DeepSeek.
- If no data can leave your machine at all, use a fully local model instead of this design.

## Setup

Create a `.env` file from `.env.example`, then put your DeepSeek key in it:

```text
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_MODEL=deepseek-chat
```

You can also set the key temporarily in PowerShell:

```powershell
$env:DEEPSEEK_API_KEY="your_deepseek_api_key_here"
```

## Easiest usage

Double-click `start_chat.bat`, or run:

```powershell
python deepseek_learning_ai.py
```

The program starts chat mode automatically. Type your questions directly:

```text
You> 用简单例子解释过拟合
You> 金融里面风险和收益是什么关系
You> exit
```

The local index is built automatically if it does not exist.

## Manual commands

Rebuild the local index after adding or changing notes:

```powershell
python deepseek_learning_ai.py index
```

Ask one question without entering chat mode:

```powershell
python deepseek_learning_ai.py ask "explain overfitting with a simple example"
```

## Add your own data

Put your computer science and finance files into `learning_data/`, then rebuild the index:

```powershell
python deepseek_learning_ai.py index
```

Supported directly:

```text
.txt .md .csv .json .yaml .yml .html .htm
.py .js .ts .tsx .jsx .java .c .cpp .h .hpp .cs .go .rs .sql
.docx .pptx .xlsx .ipynb
```

PDF files are supported after installing one package:

```powershell
python -m pip install -r requirements.txt
```

Then put `.pdf` files into `learning_data/` and rebuild the index.

## DeepSeek API

The code calls the OpenAI-compatible endpoint:

```text
POST https://api.deepseek.com/chat/completions
Authorization: Bearer $DEEPSEEK_API_KEY
```
