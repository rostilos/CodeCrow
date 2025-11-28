#!/bin/bash

set -e

echo "Setting up CodeCrow RAG Pipeline..."

# Check if Python 3.11+ is available
python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
echo "Python version: $python_version"

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv .venv

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "Installing requirements..."
pip install -r requirements.txt

# Create .env from sample if not exists
if [ ! -f .env ]; then
    echo "Creating .env from .env.sample..."
    cp .env.sample .env
    echo "Please update .env with your configuration"
fi

echo ""
echo "Setup complete!"
echo ""
echo "To activate the virtual environment, run:"
echo "  source .venv/bin/activate"
echo ""
echo "To start the API server, run:"
echo "  python main.py"
echo ""
echo "Don't forget to configure MongoDB and OpenAI API key in .env"
