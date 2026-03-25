#!/bin/bash
#===============================================================================
# Catalog Extensions - Full Automated Deployment Script
# One-command deployment to any bench/site without human intervention
#===============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_NAME="catalog_extensions"
MODULE_NAME="Catalog Extensions"

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_step "Checking prerequisites..."
    
    # Check if we're in a bench directory
    if [ ! -d "sites" ] || [ ! -f "Procfile" ]; then
        log_error "Not in a valid bench directory"
        exit 1
    fi
    
    # Check if app exists
    if [ ! -d "apps/${APP_NAME}" ]; then
        log_error "App '${APP_NAME}' not found in apps directory"
        exit 1
    fi
    
    # Check if site exists
    if [ ! -d "sites/${SITE}" ]; then
        log_error "Site '${SITE}' not found"
        exit 1
    fi
    
    # Check if bench command works
    if ! command -v bench &> /dev/null; then
        log_error "bench command not found. Are you in the bench environment?"
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# Install app on site
install_app() {
    log_step "Installing ${APP_NAME} on site ${SITE}..."
    
    # Check if already installed
    INSTALLED_APPS=$(bench --site ${SITE} list-apps 2>/dev/null || echo "")
    if echo "$INSTALLED_APPS" | grep -q "${APP_NAME}"; then
        log_warning "${APP_NAME} is already installed on ${SITE}"
        return 0
    fi
    
    # Install the app
    bench --site ${SITE} install-app ${APP_NAME}
    
    if [ $? -eq 0 ]; then
        log_success "${APP_NAME} installed successfully"
        return 0
    else
        log_error "Failed to install ${APP_NAME}"
        return 1
    fi
}

# Run migration
run_migration() {
    log_step "Running migration to create DocTypes..."
    
    bench --site ${SITE} migrate
    
    if [ $? -eq 0 ]; then
        log_success "Migration completed"
        return 0
    else
        log_error "Migration failed"
        return 1
    fi
}

# Setup DocTypes
setup_doctypes() {
    log_step "Setting up DocTypes..."
    
    # Prefer bench env Python, fall back to system python3
    local python_path="./env/bin/python"
    if [ ! -x "$python_path" ]; then
        python_path="python3"
    fi

    $python_path "apps/${APP_NAME}/deploy/setup_doctypes.py" --site ${SITE}
    
    if [ $? -eq 0 ]; then
        log_success "DocTypes setup completed"
        return 0
    else
        log_error "DocTypes setup failed"
        return 1
    fi
}

# Setup custom fields
setup_custom_fields() {
    log_step "Setting up custom fields..."
    
    # Prefer bench env Python, fall back to system python3
    local python_path="./env/bin/python"
    if [ ! -x "$python_path" ]; then
        python_path="python3"
    fi

    $python_path "apps/${APP_NAME}/deploy/setup_custom_fields.py" --site ${SITE}
    
    if [ $? -eq 0 ]; then
        log_success "Custom fields setup completed"
        return 0
    else
        log_error "Custom fields setup failed"
        return 1
    fi
}

# Clear cache
clear_cache() {
    log_step "Clearing cache..."
    
    bench --site ${SITE} clear-cache
    
    if [ $? -eq 0 ]; then
        log_success "Cache cleared"
        return 0
    else
        log_warning "Cache clear had issues (non-critical)"
        return 0
    fi
}

# Restart bench
restart_bench() {
    if [ "$SKIP_RESTART" = "true" ]; then
        log_info "Skipping bench restart (use --restart to enable)"
        return 0
    fi
    
    log_step "Restarting bench..."
    
    bench restart
    
    if [ $? -eq 0 ]; then
        log_success "Bench restarted"
        return 0
    else
        log_warning "Bench restart had issues (non-critical)"
        return 0
    fi
}

# Verify installation
verify_installation() {
    log_step "Verifying installation..."
    
    # Check if app is in installed apps
    INSTALLED_APPS=$(bench --site ${SITE} list-apps 2>/dev/null || echo "")
    if echo "$INSTALLED_APPS" | grep -q "${APP_NAME}"; then
        log_success "App is in installed apps list"
    else
        log_error "App not found in installed apps"
        return 1
    fi
    
    # Check if DocType exists (via bench execute)
    EXISTS=$(bench --site ${SITE} execute frappe.db.exists 2>/dev/null <<< '{"doctype": "DocType", "name": "Catalog Price Range"}')
    if [ "$EXISTS" = "True" ]; then
        log_success "Catalog Price Range DocType exists"
    else
        log_warning "Could not verify DocType (may need manual check)"
    fi
    
    return 0
}

# Show final instructions
show_summary() {
    echo ""
    echo "================================================================"
    echo "                 DEPLOYMENT COMPLETE!"
    echo "================================================================"
    echo ""
    echo "Site: ${SITE}"
    echo "App: ${APP_NAME}"
    echo ""
    echo "NEXT STEPS:"
    echo "-----------"
    echo "1. Access Desk and go to: ${MODULE_NAME} > Catalog Price Range"
    echo "2. Create or verify price range buckets"
    echo "3. Configure Webshop Settings if not already done"
    echo "4. Run scheduled job manually to populate badges:"
    echo "   bench --site ${SITE} execute catalog_extensions.api.recompute_item_badges"
    echo ""
    echo "VERIFICATION:"
    echo "-----------"
    echo "Check facets API: https://${SITE}/api/method/catalog_extensions.api.get_filter_facets"
    echo ""
    echo "================================================================"
}

# Usage information
usage() {
    cat << EOF
Usage: $0 [OPTIONS] --site SITE_NAME

Catalog Extensions - Full Automated Deployment

Required:
  --site SITE_NAME        Site to deploy to

Optional:
  --skip-doctypes         Skip DocType setup
  --skip-fields           Skip custom fields setup
  --skip-restart          Skip bench restart
  --restart               Enable bench restart (disabled by default)
  --help                  Show this help message

Examples:
  # Full deployment to site
  $0 --site mysite.local

  # Deploy without restarting bench
  $0 --site mysite.local --skip-restart

  # Deploy with restart
  $0 --site mysite.local --restart

EOF
    exit 1
}

# Main script
main() {
    # Parse arguments
    SITE=""
    SKIP_DOCTYPES="false"
    SKIP_FIELDS="false"
    SKIP_RESTART="true"  # Default to true for safety
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --site)
                SITE="$2"
                shift 2
                ;;
            --skip-doctypes)
                SKIP_DOCTYPES="true"
                shift
                ;;
            --skip-fields)
                SKIP_FIELDS="true"
                shift
                ;;
            --skip-restart)
                SKIP_RESTART="true"
                shift
                ;;
            --restart)
                SKIP_RESTART="false"
                shift
                ;;
            --help)
                usage
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                ;;
        esac
    done
    
    # Validate required arguments
    if [ -z "$SITE" ]; then
        log_error "Site name is required. Use --site SITE_NAME"
        usage
    fi
    
    # Banner
    echo "================================================================"
    echo "  CATALOG EXTENSIONS - AUTOMATED DEPLOYMENT"
    echo "================================================================"
    echo ""
    echo "Configuration:"
    echo "  Site: ${SITE}"
    echo "  Skip DocTypes: ${SKIP_DOCTYPES}"
    echo "  Skip Fields: ${SKIP_FIELDS}"
    echo "  Skip Restart: ${SKIP_RESTART}"
    echo ""
    echo "================================================================"
    echo ""
    
    # Execute deployment steps
    check_prerequisites || exit 1
    install_app || exit 1
    run_migration || exit 1
    
    if [ "$SKIP_DOCTYPES" != "true" ]; then
        setup_doctypes || exit 1
    fi
    
    if [ "$SKIP_FIELDS" != "true" ]; then
        setup_custom_fields || exit 1
    fi
    
    clear_cache || true
    restart_bench || true
    verify_installation || true
    
    # Show summary
    show_summary
}

# Run main
main "$@"
