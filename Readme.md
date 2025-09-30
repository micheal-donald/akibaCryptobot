2. Bootstrap the Freqtrade project
bash
Copy
# Create project folder
mkdir -p ~/freqtrade-bmad && cd ~/freqtrade-bmad

# Pull official image & skeleton
docker pull freqtradeorg/freqtrade:stable
docker run --rm -v "$PWD":/bot freqtradeorg/freqtrade:stable create-userdir --userdir user_data
docker run --rm -v "$PWD":/bot freqtradeorg/freqtrade:stable new-config --config user_data/config.json

# (Interactive wizard – choose Binance, USDT pair list, Telegram token, etc.)
3. Place the BMAD spec file
bash
Copy
cat > project_spec.md <<'EOF'
PROJECT: Freqtrade-BMAD
GOAL: End-to-end autonomous BTC/USDT spot trading bot with ≥ 1.2 profit-factor, ≤ 15 % max drawdown.
CONSTRAINTS
- Exchange: Binance Spot (paper → live)
- Strategy: start with NostalgiaForInfinityNext, then hyper-opt
- Coverage: 100 % pytest + flake8 + black
- Secrets: ENV only (.env)
- Image: freqtradeorg/freqtrade:stable < 120 MB
- Deploy: fly.io via GitHub Actions on tag stable-*
- Micro-task limit: 2 h
DELIVERABLES
1. user_data/config.json (Binance, USDT, dry_run=True initially)
2. user_data/strategies/BMADStrategy.py (post-hyper-opt)
3. user_data/hyperopts/BMADHyperOpt.py
4. tests/test_BMADStrategy.py
5. docker-compose.yml & docker-compose.prod.yml
6. .env.example (BINANCE_KEY, BINANCE_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
7. .github/workflows/deploy.yml
8. grafana/dashboard.json
SUCCESS CRITERIA
- docker-compose run --rm freqtrade backtesting --timerange 20230101-20231231 --strategy BMADStrategy → profit-factor ≥ 1.2
- docker-compose up -d → bot runs 24 h paper-trade without crash
- Telegram bot responds /start and /daily
- Grafana reachable https://<app>.fly.dev/d/freqtrade
- git log --grep BMAD- | wc -l ≥ 15
EOF
4. Spin up the BMAD fleet
bash
Copy
cd ~/freqtrade-bmad
~/orch/bmad-up.sh freqtrade-bmad
You now have these tmux sessions:
Table
Copy
Session	Purpose
orchestrator:meta	Top-level orchestrator agent
orchestrator:bmad-pm	BMAD Product Owner / Scrum-Master
freqtrade-bmad:dev-1	Developer agent (strategy, hyper-opt)
freqtrade-bmad:qa-1	QA agent (pytest, lint, dry-run)
Agents will self-schedule every 30 min and push git tags stable-* on passing CI.
5. Human checklist (once)
Telegram bot – create via @BotFather, paste token/chat-id into .env.
Binance API – create key/secret, add IP whitelist of your server, paste into .env.
fly.io – flyctl launch once (BMAD agents will automate later).
Paper-trade – let the QA agent run 24 h paper-trade before flipping dry_run: False.
6. Daily human commands
bash
Copy
# View orchestrator dashboard
tmux attach -t orchestrator

# Talk to PM
~/orch/send-claude-message.sh orchestrator:bmad-pm "Add trailing-stoploss task to backlog"

# See live bot logs
tmux attach -t freqtrade-bmad
That’s it — you now have Freqtrade running under BMAD + Tmux-Orchestrator, fully autonomous from strategy iteration to production deploy.

