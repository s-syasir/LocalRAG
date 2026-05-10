#!/usr/bin/env bash
# Quick setup for AMD GPU (ROCm). Run once on your PC.
set -e

echo "=== LocalRAG Setup ==="

# 1. Install PyTorch for ROCm (AMD GPU)
echo "[1/3] Installing PyTorch ROCm..."
pip install torch torchvision --index-url https://download.pytorch.org/whl/rocm6.2

# 2. Install remaining dependencies
echo "[2/3] Installing dependencies..."
pip install -r requirements.txt

# 3. Pull the default Ollama model
echo "[3/3] Pulling Ollama model (llama3.2)..."
ollama pull llama3.2

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. python ingest.py   →  index your notes"
echo "  2. python app.py      →  open http://localhost:7860"
echo ""
echo "  (optional) Edit .env to change the model or DB path"
