#!/bin/bash
# Monitor production deployment on Render
# Usage: ./scripts/monitor.sh <render-url> [--continuous]

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

RENDER_URL="${1}"
CONTINUOUS="${2}"

if [ -z "$RENDER_URL" ]; then
    echo "Usage: $0 <render-url> [--continuous]"
    echo "Example: $0 https://bot-webhook-xxxx.onrender.com"
    exit 1
fi

# Remove trailing slash if present
RENDER_URL="${RENDER_URL%/}"

check_health() {
    local url="$RENDER_URL/health"
    local response

    echo -e "${BLUE}→${NC} Checking health: $url"

    if response=$(curl -s -w "\n%{http_code}\n%{time_total}" "$url" 2>/dev/null); then
        http_code=$(echo "$response" | tail -n2 | head -n1)
        time_ms=$(echo "$response" | tail -n1 | awk '{printf "%.0f", $1 * 1000}')
        body=$(echo "$response" | head -n-2)

        if [ "$http_code" = "200" ]; then
            echo -e "${GREEN}✓${NC} Health OK (HTTP 200) - Response time: ${time_ms}ms"
            echo "  Response: $body"
            return 0
        else
            echo -e "${RED}✗${NC} Health check failed (HTTP $http_code)"
            echo "  Response: $body"
            return 1
        fi
    else
        echo -e "${RED}✗${NC} Failed to reach $RENDER_URL"
        return 1
    fi
}

get_service_status() {
    local service="$1"
    echo ""
    echo -e "${YELLOW}Service Status${NC}"
    echo "Check in Render dashboard:"
    echo "  Render Dashboard → $service → Status"
    echo ""
    echo "Common statuses:"
    echo "  ${GREEN}Active${NC} - Service is running"
    echo "  ${YELLOW}Deploying${NC} - New version being deployed"
    echo "  ${RED}Failed${NC} - Service crashed or build failed"
}

get_logs() {
    echo ""
    echo -e "${YELLOW}Recent Logs${NC}"
    echo "View full logs at:"
    echo "  $RENDER_URL/logs (if available)"
    echo ""
    echo "Or in Render dashboard:"
    echo "  Render Dashboard → bot-webhook → Logs"
}

get_metrics() {
    echo ""
    echo -e "${YELLOW}Performance Metrics${NC}"
    echo "View in Render dashboard:"
    echo "  Render Dashboard → bot-webhook → Metrics"
    echo ""
    echo "Watch for:"
    echo "  • CPU usage < 80%"
    echo "  • Memory usage < 90%"
    echo "  • Request latency < 500ms"
    echo "  • Error rate < 5%"
}

get_database_status() {
    echo ""
    echo -e "${YELLOW}Database Status${NC}"
    echo "Check in Render dashboard:"
    echo "  Render Dashboard → bot-db → Status"
    echo ""
    echo "Watch for:"
    echo "  • Status: Available"
    echo "  • Active connections: 2-5 (normal)"
    echo "  • No connection errors"
}

get_redis_status() {
    echo ""
    echo -e "${YELLOW}Redis Status${NC}"
    echo "Check in Render dashboard:"
    echo "  Render Dashboard → bot-redis"
    echo ""
    echo "Watch for:"
    echo "  • Status: Active"
    echo "  • Memory usage < 100MB"
    echo "  • Eviction policy: noeviction"
}

run_check() {
    clear
    echo "================================"
    echo "Production Monitoring Dashboard"
    echo "================================"
    echo ""
    echo "Service URL: $RENDER_URL"
    echo "Check time: $(date)"
    echo ""

    if check_health; then
        echo ""
        echo -e "${GREEN}═════════════════════════════════${NC}"
        echo -e "${GREEN}✓ All systems operational${NC}"
        echo -e "${GREEN}═════════════════════════════════${NC}"
    else
        echo ""
        echo -e "${RED}═════════════════════════════════${NC}"
        echo -e "${RED}✗ Service is not responding${NC}"
        echo -e "${RED}═════════════════════════════════${NC}"
        echo ""
        echo "Troubleshooting:"
        echo "1. Check if Render service is running"
        echo "2. Verify DNS can resolve the URL"
        echo "3. Check application logs for errors"
    fi

    get_service_status "bot-webhook"
    get_database_status
    get_redis_status
    get_metrics
    get_logs

    echo ""
    echo "Summary:"
    echo "  • If health check passes: Service is running correctly"
    echo "  • If health check fails: Check logs and service status"
    echo "  • Expected response time: < 100ms"
    echo ""
}

if [ "$CONTINUOUS" = "--continuous" ]; then
    echo "Starting continuous monitoring (Ctrl+C to stop)..."
    sleep 2

    while true; do
        run_check
        echo ""
        echo "Waiting 30 seconds before next check... (Ctrl+C to stop)"
        sleep 30
    done
else
    run_check
fi
