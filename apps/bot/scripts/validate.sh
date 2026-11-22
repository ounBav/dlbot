#!/bin/bash
# Configuration validation script
# Validates local setup and production readiness

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASS++))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAIL++))
}

warn() {
    echo -e "${YELLOW}!${NC} $1"
    ((WARN++))
}

info() {
    echo -e "${BLUE}→${NC} $1"
}

echo "================================"
echo "Configuration Validation"
echo "================================"
echo ""

# ===== PROJECT STRUCTURE =====
echo -e "${BLUE}📁 Project Structure${NC}"

test -f "requirements.txt" && pass "requirements.txt present" || fail "requirements.txt missing"
test -f "Dockerfile" && pass "Dockerfile present" || fail "Dockerfile missing"
test -f "render.yaml" && pass "render.yaml present" || fail "render.yaml missing"
test -f ".env.example" && pass ".env.example present" || fail ".env.example missing"
test -d "app" && pass "app/ directory present" || fail "app/ directory missing"
test -f "app/main.py" && pass "app/main.py present" || fail "app/main.py missing"
test -f "app/config.py" && pass "app/config.py present" || fail "app/config.py missing"
test -d "scripts" && pass "scripts/ directory present" || fail "scripts/ directory missing"

echo ""

# ===== GIT CONFIGURATION =====
echo -e "${BLUE}🔐 Git Configuration${NC}"

if git rev-parse --git-dir > /dev/null 2>&1; then
    pass "Git repository initialized"

    # Check .gitignore
    if grep -q "\.env" .gitignore 2>/dev/null; then
        pass ".env excluded from git"
    else
        fail ".env not in .gitignore (security risk)"
    fi

    # Check for committed secrets
    if git log --all --oneline | grep -iq "token\|password\|secret"; then
        warn "Potential secrets in git history"
    else
        pass "No obvious secrets in git history"
    fi

    # Check if .env is tracked
    if git ls-files | grep -q "^\.env$"; then
        fail ".env is tracked in git (remove with: git rm --cached .env)"
    else
        pass ".env not tracked in git"
    fi
else
    fail "Not a git repository"
fi

echo ""

# ===== DOCKERFILE CHECKS =====
echo -e "${BLUE}🐳 Docker Configuration${NC}"

if grep -q "EXPOSE 8000" Dockerfile; then
    pass "Dockerfile exposes port 8000"
else
    fail "Dockerfile must expose port 8000"
fi

if grep -q "HEALTHCHECK" Dockerfile; then
    pass "Dockerfile includes HEALTHCHECK"
else
    fail "Dockerfile missing HEALTHCHECK instruction"
fi

if grep -q "ENV PORT" Dockerfile; then
    pass "Dockerfile sets PORT environment variable"
else
    fail "Dockerfile must set PORT variable"
fi

if grep -q "rm -f .env" Dockerfile; then
    pass "Dockerfile removes .env from image"
else
    warn "Dockerfile doesn't remove .env (security concern)"
fi

if grep -q "curl" Dockerfile; then
    pass "Dockerfile includes curl for health checks"
else
    warn "Dockerfile missing curl (needed for HEALTHCHECK)"
fi

if grep -q "useradd" Dockerfile; then
    pass "Dockerfile creates non-root user"
else
    warn "Dockerfile doesn't create non-root user"
fi

echo ""

# ===== APPLICATION CODE =====
echo -e "${BLUE}📝 Application Code${NC}"

if grep -r "def health\|@.*\.get.*health" app/ >/dev/null 2>&1; then
    pass "Health check endpoint defined"
else
    fail "Health check endpoint not found in app/"
fi

if grep -r "run_migrations" app/ >/dev/null 2>&1; then
    pass "Migration logic present"
else
    fail "Migration logic not found"
fi

if grep -r "FastAPI\|fastapi" app/ >/dev/null 2>&1; then
    pass "FastAPI application detected"
else
    fail "FastAPI not found in app/"
fi

if grep -r "settings\|config\|Settings" app/config.py >/dev/null 2>&1; then
    pass "Configuration management present"
else
    fail "Configuration management not found"
fi

echo ""

# ===== REQUIREMENTS =====
echo -e "${BLUE}📦 Dependencies${NC}"

if grep -q "fastapi" requirements.txt; then
    pass "fastapi in requirements.txt"
else
    fail "fastapi not in requirements.txt"
fi

if grep -q "uvicorn" requirements.txt; then
    pass "uvicorn in requirements.txt"
else
    fail "uvicorn not in requirements.txt"
fi

if grep -q "psycopg" requirements.txt; then
    pass "psycopg in requirements.txt"
else
    fail "psycopg not in requirements.txt"
fi

if grep -q "redis" requirements.txt; then
    pass "redis in requirements.txt"
else
    fail "redis not in requirements.txt"
fi

echo ""

# ===== RENDER BLUEPRINT =====
echo -e "${BLUE}☁️  Render Blueprint${NC}"

if grep -q "type: web" render.yaml; then
    pass "Web service defined"
else
    fail "Web service not defined in render.yaml"
fi

if grep -q "type: worker" render.yaml; then
    pass "Worker service defined"
else
    fail "Worker service not defined in render.yaml"
fi

if grep -q "type: postgres\|databases:" render.yaml; then
    pass "Database service defined"
else
    fail "Database service not defined in render.yaml"
fi

if grep -q "type: keyvalue\|keyValue:" render.yaml; then
    pass "Redis service defined"
else
    fail "Redis service not defined in render.yaml"
fi

if grep -q "healthCheckPath: /health" render.yaml; then
    pass "Health check path configured"
else
    warn "Health check path not configured"
fi

echo ""

# ===== LOCAL DEVELOPMENT =====
echo -e "${BLUE}🛠️  Local Development${NC}"

if [ -f ".env" ]; then
    pass ".env exists for local development"
else
    info ".env file not present (create from .env.example for local testing)"
fi

if [ -f ".env.example" ]; then
    pass ".env.example present (reference for setup)"
else
    fail ".env.example missing"
fi

echo ""

# ===== DOCUMENTATION =====
echo -e "${BLUE}📚 Documentation${NC}"

test -f "DEPLOY.md" && pass "DEPLOY.md present" || fail "DEPLOY.md missing"
test -f "TROUBLESHOOTING.md" && pass "TROUBLESHOOTING.md present" || fail "TROUBLESHOOTING.md missing"
test -f "IMPROVEMENTS.md" && pass "IMPROVEMENTS.md present" || fail "IMPROVEMENTS.md missing"
test -f "README.md" && pass "README.md present" || fail "README.md missing"

echo ""

# ===== SCRIPTS =====
echo -e "${BLUE}⚙️  Deployment Scripts${NC}"

test -f "scripts/deploy.sh" && pass "scripts/deploy.sh present" || fail "scripts/deploy.sh missing"
test -f "scripts/monitor.sh" && pass "scripts/monitor.sh present" || fail "scripts/monitor.sh missing"
test -f "scripts/validate.sh" && pass "scripts/validate.sh present" || fail "scripts/validate.sh missing"
test -f "scripts/set_webhook.py" && pass "scripts/set_webhook.py present" || fail "scripts/set_webhook.py missing"

echo ""

# ===== SUMMARY =====
echo "================================"
echo "Validation Summary"
echo "================================"
echo -e "${GREEN}Passed: $PASS${NC}"
echo -e "${YELLOW}Warnings: $WARN${NC}"
echo -e "${RED}Failed: $FAIL${NC}"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✓ All validation checks passed!${NC}"
    echo ""
    echo "Next steps:"
    echo "1. For local development: python -m venv .venv && .venv/Scripts/activate"
    echo "2. Install deps: pip install -r requirements.txt"
    echo "3. Run tests: pytest"
    echo "4. For production: ./scripts/deploy.sh"
    exit 0
else
    echo -e "${RED}✗ Fix the failed checks above${NC}"
    exit 1
fi
