#!/bin/bash
# Helper script to reconfigure Proxmox API credentials in vault.yml
# This script decrypts, updates, and re-encrypts the vault file

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

# Helper functions
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

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Configure Proxmox API Credentials                       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo

VAULT_FILE="inventory/group_vars/all/vault.yml"
VAULT_BACKUP="$VAULT_FILE.backup.$(date +%s)"

# Check if vault file exists
if [ ! -f "$VAULT_FILE" ]; then
    print_warning "Vault file does not exist: $VAULT_FILE"
    print_info "Creating new vault file..."
    CREATE_NEW=true
else
    # Check if encrypted
    if head -n1 "$VAULT_FILE" | grep -q '$ANSIBLE_VAULT'; then
        print_info "Current vault is encrypted"
        IS_ENCRYPTED=true
    else
        print_warning "Current vault is NOT encrypted"
        IS_ENCRYPTED=false
    fi
    CREATE_NEW=false
fi

# Prompt for credentials
echo
print_info "Enter new Proxmox API credentials"
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

echo
print_status "Credentials captured"

# Backup existing vault if it exists
if [ "$CREATE_NEW" = false ]; then
    if [ "$IS_ENCRYPTED" = true ]; then
        print_info "Decrypting existing vault..."
        uv run --locked ansible-vault decrypt "$VAULT_FILE"
        print_status "Vault decrypted"
    fi
    
    cp "$VAULT_FILE" "$VAULT_BACKUP"
    print_status "Backed up existing vault to: $VAULT_BACKUP"
fi

# Create new vault content
print_info "Writing new credentials to vault..."

cat > "$VAULT_FILE" << EOF
---
# Proxmox API authentication details
# Updated by configure-vault.sh on $(date)

vault_proxmox_api_user: "$PROXMOX_USER"
vault_proxmox_api_token_id: "$PROXMOX_TOKEN_ID"
vault_proxmox_api_token_secret: "$PROXMOX_TOKEN_SECRET"
EOF

print_status "Vault file updated"

# Encrypt the vault
print_info "Encrypting vault..."
uv run --locked ansible-vault encrypt "$VAULT_FILE"
print_status "Vault encrypted"

echo
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Credentials Updated Successfully!                        ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo
echo "You can now test connectivity with:"
echo -e "${BLUE}  ./run.sh --tags validation${NC}"
echo

if [ "$CREATE_NEW" = false ]; then
    echo "Previous vault backed up to:"
    echo -e "${BLUE}  $VAULT_BACKUP${NC}"
    echo
fi
