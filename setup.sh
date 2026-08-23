#!/bin/bash
# Automated workstation setup for homelab-iac
# This script prepares a fresh workstation to run Ansible playbooks

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Determine project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  homelab-iac - Controller Setup                            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo

# Function to print status
print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    print_error "Do not run this script as root. It will request sudo when needed."
    exit 1
fi

echo "Step 1: Checking system prerequisites..."
echo "─────────────────────────────────────────"

# Check for required commands
MISSING_PACKAGES=()

if ! command -v python3 &> /dev/null; then
    print_error "python3 not found"
    MISSING_PACKAGES+=("python3")
fi

if ! command -v curl &> /dev/null; then
    print_warning "curl not installed"
    MISSING_PACKAGES+=("curl")
fi

if ! dpkg -l | grep -q sshpass; then
    print_warning "sshpass not installed"
    MISSING_PACKAGES+=("sshpass")
fi

# Install missing packages
if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo
    print_info "Installing missing packages: ${MISSING_PACKAGES[*]}"
    echo "This requires sudo access..."
    sudo apt update
    sudo apt install -y "${MISSING_PACKAGES[@]}"
    print_status "Packages installed"
else
    print_status "All system prerequisites satisfied"
fi

echo
echo "Step 2: Setting up uv..."
echo "─────────────────────────────────────────"

print_info "On the managed workstation LXC, run workstation-setup first; Home Manager supplies uv there."

if ! command -v uv &> /dev/null; then
    print_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    print_status "uv installed"
else
    print_status "uv already installed ($(uv --version))"
fi

echo
echo "Step 3: Setting up project structure..."
echo "─────────────────────────────────────────"

# Create .ansible directory if it doesn't exist
mkdir -p .ansible/cp
mkdir -p .ansible/cache
print_status "Created .ansible directories"

# Machine-local SSH key directory (shared across git worktrees)
mkdir -p "$HOME/.ansible/ssh"
chmod 700 "$HOME/.ansible" "$HOME/.ansible/ssh"
print_status "Ensured $HOME/.ansible/ssh exists"

echo
echo "Step 4: Vault password configuration..."
echo "─────────────────────────────────────────"

VAULT_PASS_FILE="$HOME/.ansible/vault-pass"

mkdir -p "$HOME/.ansible"
chmod 700 "$HOME/.ansible"

if [ -f "$VAULT_PASS_FILE" ]; then
    print_status "Using existing vault password at $VAULT_PASS_FILE"
else
    print_error "Vault password file missing at $VAULT_PASS_FILE"
    print_info "Provision it first via chezmoi + Bitwarden, then rerun setup:"
    echo "  bw login"
    echo "  export BW_SESSION=\$(bw unlock --raw)"
    echo "  chezmoi init --apply https://github.com/faviann/dotfiles.git"
    exit 1
fi

echo
echo "Step 5: Proxmox API credentials..."
echo "─────────────────────────────────────────"

# Prompt for Proxmox credentials
echo
print_info "Enter your Proxmox API credentials"
echo "These will be stored in an encrypted vault file."
echo

# Proxmox API host
read -p "Proxmox API host [proxmox.lan]: " PROXMOX_HOST
PROXMOX_HOST=${PROXMOX_HOST:-proxmox.lan}

# Proxmox API user
echo
print_info "API user format: username@realm (e.g., root@pam, ansible@pve)"
read -p "Proxmox API user [root@pam]: " PROXMOX_USER
PROXMOX_USER=${PROXMOX_USER:-root@pam}

# Proxmox API token ID
echo
print_info "Token ID is the name you gave the token (e.g., ansible-automation)"
read -p "Proxmox API token ID: " PROXMOX_TOKEN_ID

while [ -z "$PROXMOX_TOKEN_ID" ]; do
    print_warning "Token ID cannot be empty"
    read -p "Proxmox API token ID: " PROXMOX_TOKEN_ID
done

# Proxmox API token secret
echo
print_info "Token secret is the UUID shown when creating the token"
read -sp "Proxmox API token secret: " PROXMOX_TOKEN_SECRET
echo

while [ -z "$PROXMOX_TOKEN_SECRET" ]; do
    print_warning "Token secret cannot be empty"
    read -sp "Proxmox API token secret: " PROXMOX_TOKEN_SECRET
    echo
done

print_status "Credentials captured"

echo
echo "Step 6: Installing Python dependencies..."
echo "─────────────────────────────────────────"

print_info "Running uv sync --locked..."
uv sync --locked
print_status "Python dependencies installed"

echo
echo "Step 7: Running bootstrap playbook..."
echo "─────────────────────────────────────────"

if [ ! -f "bootstrap.yml" ]; then
    print_error "bootstrap.yml not found in $PROJECT_ROOT"
    exit 1
fi

uv run --locked ansible-playbook bootstrap.yml
print_status "Bootstrap completed"

echo
echo "Step 8: Creating and encrypting vault..."
echo "─────────────────────────────────────────"

VAULT_FILE="inventory/group_vars/all/vault.yml"

if [ -f "$VAULT_FILE" ]; then
    # Check if already encrypted
    if head -n1 "$VAULT_FILE" | grep -q '$ANSIBLE_VAULT'; then
        print_status "Vault file already exists and is encrypted"
        print_info "To update credentials, run: ./configure-vault.sh"
    else
        print_warning "Vault file exists but is NOT encrypted"
        read -p "Replace with new credentials and encrypt? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm "$VAULT_FILE"
        else
            print_info "Keeping existing unencrypted vault"
            echo
            read -p "Encrypt existing vault? (y/n): " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                uv run --locked ansible-vault encrypt "$VAULT_FILE"
                print_status "Vault encrypted"
            fi
        fi
    fi
fi

if [ ! -f "$VAULT_FILE" ]; then
    print_info "Creating vault.yml with your credentials..."
    
    # Create vault file with captured credentials
    cat > "$VAULT_FILE" << EOF
---
# Proxmox API authentication details
# Generated by setup.sh on $(date)

vault_proxmox_api_user: "$PROXMOX_USER"
vault_proxmox_api_token_id: "$PROXMOX_TOKEN_ID"
vault_proxmox_api_token_secret: "$PROXMOX_TOKEN_SECRET"
EOF
    
    print_status "Created vault.yml"
    
    # Encrypt the vault file
    print_info "Encrypting vault.yml..."
    uv run --locked ansible-vault encrypt "$VAULT_FILE"
    print_status "Vault encrypted and ready to use"
fi

echo
echo "Step 9: Claude Code skills..."
echo "─────────────────────────────────────────"

SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
for skill in "$PROJECT_ROOT/.agents/skills"/*/; do
    skill_name="$(basename "$skill")"
    target="$SKILLS_DIR/$skill_name"
    if [ -L "$target" ] || [ -e "$target" ]; then
        print_status "Skill already linked: $skill_name"
    else
        ln -s "$skill" "$target"
        print_status "Linked skill: $skill_name"
    fi
done

echo
echo "Step 10: VS Code configuration..."
echo "─────────────────────────────────────────"

if command -v code &> /dev/null || [ -d "$HOME/.vscode" ]; then
    mkdir -p .vscode
    cat > .vscode/settings.json << 'EOF'
{
  "terminal.integrated.profiles.linux": {
    "bash": { "path": "bash", "args": ["-l"] }
  },
  "terminal.integrated.defaultProfile.linux": "bash"
}
EOF
    print_status "VS Code terminal configured"
else
    print_info "VS Code not detected — skipping .vscode/settings.json"
fi

echo
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Setup Complete!                                          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo
echo "Project is ready to use. Quick reference:"
echo
echo "  • Sync environment:       ${BLUE}uv sync --locked${NC}"
echo "  • Test connectivity:      ${BLUE}./run.sh --tags validation${NC}"
echo "  • Update credentials:     ${BLUE}./configure-vault.sh${NC}"
echo "  • Edit encrypted vault:   ${BLUE}uv run --locked ansible-vault edit inventory/group_vars/all/vault.yml${NC}"
echo
echo "Documentation:"
echo "  • Main README:             ${BLUE}README.md${NC}"
echo "  • Agent instructions:      ${BLUE}AGENTS.md${NC}"
echo
