#!/bin/bash
# Guided workstation setup, plus targeted controller reconciliation.

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

usage() {
    cat <<'EOF'
Usage: ./setup.sh [operation]

With no operation, run guided workstation setup.

Operations:
  sync       Synchronize the locked controller environment only
  bootstrap  Reconcile Ansible collections, external roles, and the
             controller SSH key, creating the key only when absent
EOF
}

usage_error() {
    echo "setup.sh: $1" >&2
    echo "Try './setup.sh --help' for usage." >&2
    exit 2
}

# The locked environment is ready when uv has materialized the project venv --
# the same signal playbooks/controller-prerequisites.yml asserts on.
LOCKED_ENVIRONMENT="$PROJECT_ROOT/.venv"

sync_locked_environment() {
    if ! command -v uv &> /dev/null; then
        print_error "uv not found on PATH"
        print_info "Install uv from https://astral.sh/uv, or run ./setup.sh for the guided path."
        return 1
    fi
    print_info "Synchronizing the locked controller environment..."
    uv sync --locked || return 1
    print_status "Locked environment synchronized"
}

reconcile_controller_artifacts() {
    if [ ! -d "$LOCKED_ENVIRONMENT" ]; then
        print_error "Locked environment missing at $LOCKED_ENVIRONMENT"
        print_info "Run ./setup.sh sync first."
        return 1
    fi
    if [ ! -f "bootstrap.yml" ]; then
        print_error "bootstrap.yml not found in $PROJECT_ROOT"
        return 1
    fi
    print_info "Reconciling collections, external roles, and the controller SSH key..."
    # --no-sync keeps this operation out of sync's territory: without it, uv
    # materializes or repairs the locked environment here, so bootstrap would
    # silently do the dependency synchronization it is supposed to require.
    uv run --no-sync --locked ansible-playbook bootstrap.yml || return 1
    print_status "Controller artifacts reconciled"
}

operation="${1-}"
case "$operation" in
    "" | --help | sync | bootstrap) ;;
    -*)
        usage_error "unknown option"
        ;;
    *)
        usage_error "unknown operation"
        ;;
esac

# No form of this command takes an argument beyond its operation, and the
# guided default takes none at all.
(($# <= 1)) || usage_error "$operation takes no arguments"

case "$operation" in
    --help)
        usage
        exit 0
        ;;
    sync)
        sync_locked_environment || exit 1
        exit 0
        ;;
    bootstrap)
        reconcile_controller_artifacts || exit 1
        exit 0
        ;;
esac

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  homelab-iac - Controller Setup                            ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo

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
echo "Step 5: Installing Python dependencies..."
echo "─────────────────────────────────────────"

sync_locked_environment

echo
echo "Step 6: Reconciling controller artifacts..."
echo "─────────────────────────────────────────"

reconcile_controller_artifacts

echo
echo "Step 7: Proxmox API credentials..."
echo "─────────────────────────────────────────"

VAULT_FILE="inventory/group_vars/all/vault.yml"

if [ -f "$VAULT_FILE" ] && head -n1 "$VAULT_FILE" | grep -q '$ANSIBLE_VAULT'; then
    print_status "Encrypted vault already present — leaving it untouched"
    print_info "To change credentials later, run: ./vault.sh configure"
else
    if [ -f "$VAULT_FILE" ]; then
        print_warning "Vault file exists but is NOT encrypted: $VAULT_FILE"
        print_info "./vault.sh configure offers to encrypt and replace it."
    else
        print_info "No vault file found at $VAULT_FILE"
    fi
    read -p "Set up Proxmox API credentials now with ./vault.sh configure? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if ! ./vault.sh configure; then
            print_warning "Vault configuration did not complete"
            print_info "Run ./vault.sh configure when you are ready."
        fi
    else
        print_info "Run ./vault.sh configure when you are ready."
    fi
fi

echo
echo "Step 8: Claude Code skills..."
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
echo "Step 9: VS Code configuration..."
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
echo "  • Sync the locked environment:  ./setup.sh sync"
echo "  • Reconcile controller state:   ./setup.sh bootstrap"
echo "  • Verify the vault:             ./vault.sh check"
echo "  • Update credentials:           ./vault.sh configure"
echo "  • Edit the encrypted vault:     ./vault.sh edit"
echo "  • Check fleet connectivity:     ./inspect.sh connectivity"
echo "  • Deploy the fleet:             ./run.sh"
echo
echo "Documentation:"
echo "  • Main README:                  README.md"
echo "  • Agent instructions:           AGENTS.md"
echo
