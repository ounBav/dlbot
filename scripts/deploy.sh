#!/bin/bash
# Production deployment script for Render
# Usage: ./scripts/deploy.sh [--check-only] [--url <render-url>]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
CHECK_ONLY=false
RENDER_URL="${1}"

print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}!${NC} $1"
}

print_info() {
    echo -e "${YELLOW}→${NC} $1"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --check-only)
            CHECK_ONLY=true
            shift
            ;;
        --url)
            RENDER_URL="$2"
            shift 2
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Usage: $0 [--check-only] [--url <render-url>]"
            exit 1
            ;;
    esac
done

echo "================================"
echo "Production Deployment Check"
echo "================================"
echo ""

# Check 1: Git status
print_info "Checking git status..."
if ! git diff-index --quiet HEAD --; then
    print_error "Uncommitted changes detected"
    echo "Commit your changes before deploying:"
    git status --short
    exit 1
fi
print_status "No uncommitted changes"

# Check 2: .env not in git
print_info "Verifying .env is not tracked..."
if git ls-files | grep -q "\.env$"; then
    print_error ".env file is tracked in git (security risk)"
    exit 1
fi
print_status ".env not tracked in git"

# Check 3: Secrets not in git history
print_info "Checking git history for secrets..."
if git log --all --grep="TOKEN\|PASSWORD\|SECRET" -i --oneline | head -5 | grep -q .; then
    print_warning "Found potential secrets in commit messages"
    echo "Review recent commits:"
    git log --all --grep="TOKEN\|PASSWORD\|SECRET" -i --oneline | head -5
fi

# Check 4: requirements.txt exists
print_info "Checking requirements.txt..."
if [ ! -f "requirements.txt" ]; then
    print_error "requirements.txt not found"
    exit 1
fi
print_status "requirements.txt present"

# Check 5: Dockerfile valid
print_info "Checking Dockerfile..."
if [ ! -f "Dockerfile" ]; then
    print_error "Dockerfile not found"
    exit 1
fi
if ! grep -q "EXPOSE 8000" Dockerfile; then
    print_warning "Dockerfile doesn't expose port 8000"
fi
if ! grep -q "HEALTHCHECK" Dockerfile; then
    print_warning "Dockerfile missing HEALTHCHECK"
fi
print_status "Dockerfile present"

# Check 6: render.yaml valid
print_info "Checking render.yaml..."
if [ ! -f "render.yaml" ]; then
    print_error "render.yaml not found"
    exit 1
fi
print_status "render.yaml present"

# Check 7: Health check endpoint exists
print_info "Checking for /health endpoint..."
if ! grep -r "def health\|@.*\.get.*health" --include="*.py" . >/dev/null 2>&1; then
    print_error "Health check endpoint not found"
    exit 1
fi
print_status "Health check endpoint defined"

# Check 8: Run pytest if not check-only
if [ "$CHECK_ONLY" = false ]; then
    print_info "Running tests..."
    if ! python -m pytest -q; then
        print_error "Tests failed"
        exit 1
    fi
    print_status "All tests passed"
fi

echo ""
echo "================================"
print_status "Pre-deployment checks passed"
echo "================================"
echo ""

if [ -z "$RENDER_URL" ]; then
    echo "Next steps:"
    echo "1. Push to GitHub:"
    echo "   git push origin main"
    echo ""
    echo "2. Render will auto-deploy"
    echo "   (or manually trigger in Render dashboard)"
    echo ""
    echo "3. Verify deployment:"
    echo "   ./scripts/deploy.sh --check-only --url https://bot-webhook-xxxx.onrender.com"
else
    print_info "Verifying deployment at: $RENDER_URL"
    echo ""

    # Check health endpoint
    print_info "Checking /health endpoint..."
    if response=$(curl -s -w "\n%{http_code}" "$RENDER_URL/health" 2>/dev/null); then
        http_code=$(echo "$response" | tail -n1)
        body=$(echo "$response" | head -n-1)

        if [ "$http_code" = "200" ]; then
            print_status "Health check passed (HTTP 200)"
            echo "Response: $body"
        else
            print_error "Health check failed (HTTP $http_code)"
            exit 1
        fi
    else
        print_error "Failed to reach $RENDER_URL"
        exit 1
    fi

    echo ""
    print_status "Deployment verified successfully"
fi

echo ""
echo "For more details, see: DEPLOY.md"
