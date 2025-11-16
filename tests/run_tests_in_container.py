#!/usr/bin/env python3
"""
Test runner script that executes INSIDE the container.
Provides clear, verbose output for AI monitoring via docker logs.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ANSI colors for docker logs
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BOLD = '\033[1m'
RESET = '\033[0m'


def log(message: str, level: str = "INFO"):
    """Print timestamped, colored log messages."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    colors = {
        "ERROR": RED,
        "SUCCESS": GREEN, 
        "WARNING": YELLOW,
        "PROGRESS": CYAN,
    }
    
    icons = {
        "ERROR": "❌",
        "SUCCESS": "✅",
        "WARNING": "⚠️",
        "PROGRESS": "🔄",
        "INFO": "ℹ️",
    }
    
    color = colors.get(level, "")
    icon = icons.get(level, "")
    
    print(f"{color}{BOLD}[{timestamp}] {icon} {message}{RESET}", flush=True)


def run_command(cmd: list, description: str, timeout: int = 300) -> bool:
    """Run a command with timeout and live output."""
    log(f"Running: {description}", "PROGRESS")
    log(f"Command: {' '.join(cmd)}", "INFO")
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        
        start_time = time.time()
        last_output = start_time
        output_lines = []
        
        while True:
            # Check timeout
            if time.time() - start_time > timeout:
                log(f"TIMEOUT: {description} exceeded {timeout}s", "ERROR")
                process.kill()
                return False
                
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
                
            if line:
                line = line.rstrip()
                output_lines.append(line)
                
                # Highlight important lines
                if "PASSED" in line:
                    print(f"  {GREEN}✓ {line}{RESET}", flush=True)
                elif "FAILED" in line or "ERROR" in line:
                    print(f"  {RED}✗ {line}{RESET}", flush=True)
                elif "test_" in line or "::test_" in line:
                    print(f"  {CYAN}→ {line}{RESET}", flush=True)
                elif "=" * 10 in line:
                    print(f"  {line}", flush=True)
                else:
                    # Regular output
                    print(f"    {line}", flush=True)
                    
                last_output = time.time()
                
            # Detect hanging
            if time.time() - last_output > 30:
                log(f"WARNING: No output for 30s from {description}", "WARNING")
                last_output = time.time()
                
        # Get result
        return_code = process.poll()
        duration = time.time() - start_time
        
        if return_code == 0:
            log(f"SUCCESS: {description} completed in {duration:.1f}s", "SUCCESS")
            return True
        else:
            log(f"FAILED: {description} (exit code {return_code}) after {duration:.1f}s", "ERROR")
            # Show last few lines for debugging
            if output_lines:
                print("\nLast 10 lines of output:", flush=True)
                for line in output_lines[-10:]:
                    print(f"  {line}", flush=True)
            return False
            
    except Exception as e:
        log(f"EXCEPTION in {description}: {e}", "ERROR")
        return False


def main():
    """Main test execution inside container."""
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}QUADRACODE E2E TEST RUNNER (CONTAINERIZED){RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    
    # Show environment  
    log("Container Environment:", "INFO")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  Working Dir: {os.getcwd()}")
    print(f"  Redis Host: {os.environ.get('REDIS_HOST', 'localhost')}")
    print(f"  Registry URL: {os.environ.get('AGENT_REGISTRY_URL', 'http://localhost:8090')}")
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if api_key.startswith('sk-'):
        print(f"  API Key: ✓ Set (length: {len(api_key)})")
    else:
        print(f"  API Key: ✗ Not set or invalid")
        log("WARNING: ANTHROPIC_API_KEY not set. Tests requiring LLM will fail.", "WARNING")
    print()
    
    # Determine test mode
    test_mode = os.environ.get("TEST_MODE", "smoke").lower()
    log(f"Test Mode: {test_mode.upper()}", "INFO")
    
    # Test directory
    test_dir = Path("/app/tests/e2e_advanced")
    if not test_dir.exists():
        log(f"Test directory not found: {test_dir}", "ERROR")
        return 1
        
    # Define test suites
    if test_mode == "smoke":
        tests = [
            ("test_foundation_smoke.py::test_imports_and_structure", 30),
            ("test_foundation_smoke.py::test_logging_infrastructure", 30),
            ("test_foundation_smoke.py::test_metrics_collector_workflow", 30),
            ("test_foundation_smoke.py::test_timeout_manager_integration", 30),
            ("test_foundation_smoke.py::test_polling_utilities", 30),
            ("test_foundation_smoke.py::test_artifact_capture", 30),
        ]
        log("Running SMOKE tests (no LLM calls, quick validation)", "INFO")
    elif test_mode == "integration":
        tests = [
            ("test_foundation_smoke.py", 300),
        ]
        log("Running INTEGRATION tests (includes container operations)", "WARNING")
    elif test_mode == "full":
        tests = [
            ("test_foundation_smoke.py", 300),
            ("test_foundation_long_run.py", 600),
            ("test_context_engine_stress.py", 900),
            ("test_prp_autonomous.py", 1200),
            ("test_fleet_management.py", 600),
            ("test_workspace_integrity.py", 900),
            ("test_observability.py", 900),
        ]
        log("Running FULL test suite (60-90 minutes)", "WARNING")
    else:
        log(f"Unknown test mode: {test_mode}", "ERROR")
        return 1
        
    # Run tests
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}STARTING TEST EXECUTION{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    
    passed = 0
    failed = 0
    
    for test_file, timeout in tests:
        print(f"\n{CYAN}{BOLD}--- Test: {test_file} ---{RESET}\n")
        
        cmd = [
            "python", "-m", "pytest",
            f"{test_dir}/{test_file}",
            "-v", "-s",
            "--tb=short",
            "--color=yes",
            "-o", "log_cli=true",
            "-o", "log_cli_level=INFO",
        ]
        
        if run_command(cmd, test_file, timeout):
            passed += 1
        else:
            failed += 1
            # Ask if we should continue (for AI, always continue)
            log("Continuing with remaining tests...", "INFO")
            
    # Summary
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}TEST SUMMARY{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")
    
    total = passed + failed
    if failed == 0:
        log(f"ALL TESTS PASSED! ({passed}/{total})", "SUCCESS")
        exit_code = 0
    else:
        log(f"TESTS FAILED: {failed}/{total} failed", "ERROR")
        exit_code = 1
        
    print(f"\n{GREEN}✅ Passed: {passed}{RESET}")
    print(f"{RED}❌ Failed: {failed}{RESET}")
    print(f"Total: {total} tests\n")
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())