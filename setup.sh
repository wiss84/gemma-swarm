#!/usr/bin/env bash
# =============================================================================
# Gemma Swarm — Setup Script (macOS / Linux)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Checking for Conda installation..."

# Locate conda
if   [[ -f "$HOME/anaconda3/bin/conda" ]];  then CONDA_PATH="$HOME/anaconda3"
elif [[ -f "$HOME/miniconda3/bin/conda" ]]; then CONDA_PATH="$HOME/miniconda3"
elif [[ -f "/opt/anaconda3/bin/conda" ]];   then CONDA_PATH="/opt/anaconda3"
elif [[ -f "/opt/miniconda3/bin/conda" ]];  then CONDA_PATH="/opt/miniconda3"
elif command -v conda &>/dev/null;          then CONDA_PATH="$(conda info --base)"
else
    echo "Conda not found. Installing Miniconda..."
    INSTALLER="miniconda.sh"

    if [[ "$(uname)" == "Darwin" ]]; then
        curl -fsSL "https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh" -o "$INSTALLER"
    else
        curl -fsSL "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" -o "$INSTALLER"
    fi

    bash "$INSTALLER" -b -p "$HOME/miniconda3"
    rm -f "$INSTALLER"
    CONDA_PATH="$HOME/miniconda3"
fi

echo "Using Conda at: $CONDA_PATH"

# Initialize conda for this shell session
# shellcheck source=/dev/null
source "$CONDA_PATH/etc/profile.d/conda.sh"

# ── gemma_swarm environment ────────────────────────────────────────────────────
echo "Checking if gemma_swarm environment exists..."

if conda env list | grep -q "gemma_swarm"; then
    echo "gemma_swarm environment already exists. Skipping creation."
else
    echo "Creating environment gemma_swarm..."
    conda create -y -n gemma_swarm python=3.11
fi

echo "Activating gemma_swarm..."
conda activate gemma_swarm

echo "Installing requirements..."
pip install -r "$SCRIPT_DIR/requirements.txt"

# ── gemma_test environment (coding agent sandbox) ─────────────────────────────
echo ""
echo "Checking if gemma_test environment exists..."

if conda env list | grep -q "gemma_test"; then
    echo "gemma_test environment already exists. Skipping creation."
else
    echo "Creating environment gemma_test..."
    conda create -y -n gemma_test python=3.11
fi

echo "Installing coding agent dependencies into gemma_test..."
conda run -n gemma_test pip install pytest ruff flake8 mypy magika

echo "gemma_test environment ready."

# ── Node.js (required for JS/TS project validation) ────────────────────────────
echo ""
echo "Checking for Node.js installation..."

if command -v node &>/dev/null; then
    echo "Node.js already installed. Skipping."
    if ! command -v tsc &>/dev/null; then
        echo "TypeScript compiler not found. Installing TypeScript and eslint globally..."
        npm install -g typescript eslint
    else
        echo "TypeScript compiler already installed. Skipping."
    fi
else
    echo "Node.js not found. Installing Node.js LTS..."
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install node
        else
            echo "WARNING: Homebrew not found. Install Node.js manually from https://nodejs.org/"
        fi
    else
        # Linux (Debian/Ubuntu)
        if command -v apt-get &>/dev/null; then
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt-get install -y nodejs
        else
            echo "WARNING: apt-get not found. Install Node.js manually from https://nodejs.org/"
        fi
    fi

    if command -v node &>/dev/null; then
        echo "Node.js installed successfully."
        echo "Installing TypeScript and eslint globally..."
        npm install -g typescript eslint
    else
        echo "WARNING: Node.js installation failed. JS/TS validation will not be available."
    fi
fi

# ── TS Analysis Bridge (ts-morph for semantic JS/TS analysis) ─────────────────
echo ""
echo "Installing ts-morph for JS/TS semantic analysis bridge..."
BRIDGE_DIR="$SCRIPT_DIR/tools/ts_analysis_bridge"
if [[ -f "$BRIDGE_DIR/package.json" ]]; then
    pushd "$BRIDGE_DIR" > /dev/null
    if npm install --prefer-offline; then
        echo "ts-morph bridge ready."
    else
        echo "WARNING: ts-morph installation failed. JS/TS semantic analysis will not be available."
    fi
    popd > /dev/null
else
    echo "WARNING: ts_analysis_bridge not found, skipping."
fi

# ── Launcher script ────────────────────────────────────────────────────────────
LAUNCHER="$SCRIPT_DIR/gemma-swarm.sh"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
echo "Starting Gemma Swarm..."
source "$CONDA_PATH/etc/profile.d/conda.sh"
conda activate gemma_swarm
cd "$SCRIPT_DIR"
python slack_app.py
EOF

chmod +x "$LAUNCHER"
echo "Created launcher: $LAUNCHER"

# ── Desktop shortcut (macOS only) ─────────────────────────────────────────────
if [[ "$(uname)" == "Darwin" ]]; then
    SHORTCUT="$HOME/Desktop/Gemma-Swarm.command"
    cp "$LAUNCHER" "$SHORTCUT"
    chmod +x "$SHORTCUT"
    echo "Created desktop shortcut: $SHORTCUT"
fi

echo ""
echo "==============================="
echo " Setup completed successfully! "
echo "==============================="
echo ""
echo "To start Gemma Swarm, run:"
echo "  bash $LAUNCHER"
