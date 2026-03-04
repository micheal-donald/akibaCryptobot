import subprocess
import sys

def run_cmd(cmd):
    print("Running:", cmd)
    subprocess.run(cmd, shell=True)

# Create flake8 config for black compatibility
with open(".flake8", "w") as f:
    f.write("[flake8]\nmax-line-length = 88\nextend-ignore = E203, W503\n")

# Run autoflake
run_cmd("pip install autoflake")
run_cmd("autoflake --in-place --remove-all-unused-imports --remove-unused-variables -r src/ tests/")

# Use autopep8 for remaining things if needed, or simply run ruff
run_cmd("pip install ruff")
run_cmd("ruff check --fix src/ tests/")
run_cmd("ruff format src/ tests/") # This matches black
