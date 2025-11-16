#!/usr/bin/env python3
"""
AI-friendly test runner with verbose progress reporting.
This script runs E2E tests with clear status updates that can be easily monitored.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ANSI color codes for clear output
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
MAGENTA = '\033[95m'
CYAN = '\033[96m'
WHITE = '\033[97m'
RESET = '\033[0m'
BOLD = '\033[1m'


class AITestRunner:
    """Test runner optimized for AI monitoring."""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.start_time = time.time()
        self.test_results: Dict[str, dict] = {}
        
    def log(self, message: str, level: str = "INFO", prefix: str = ""):
        """Log with timestamp and clear formatting."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        color = WHITE
        if level == "ERROR":
            color = RED
        elif level == "SUCCESS":
            color = GREEN
        elif level == "WARNING":
            color = YELLOW
        elif level == "PROGRESS":
            color = CYAN
        elif level == "SECTION":
            color = MAGENTA
            
        icon = {
            "ERROR": "❌",
            "SUCCESS": "✅", 
            "WARNING": "⚠️",
            "PROGRESS": "🔄",
            "SECTION": "📋",
            "INFO": "ℹ️"
        }.get(level, "  ")
        
        if prefix:
            prefix = f"[{prefix}] "
            
        print(f"{color}{BOLD}[{timestamp}] {icon} {prefix}{message}{RESET}")
        sys.stdout.flush()
        
    def section(self, title: str):
        """Print a clear section header."""
        print("\n" + "=" * 80)
        self.log(title.upper(), "SECTION")
        print("=" * 80 + "\n")
        sys.stdout.flush()
        
    def check_prerequisites(self) -> bool:
        """Check all prerequisites with clear reporting."""
        self.section("Prerequisites Check")
        
        checks = [
            ("Docker", self._check_docker),
            ("Docker Compose", self._check_docker_compose),
            ("API Keys", self._check_api_keys),
            ("Redis Connection", self._check_redis),
            ("Agent Registry", self._check_registry),
        ]
        
        all_passed = True
        for name, check_func in checks:
            self.log(f"Checking {name}...", "PROGRESS")
            success, message = check_func()
            if success:
                self.log(f"{name}: {message}", "SUCCESS")
            else:
                self.log(f"{name}: {message}", "ERROR")
                all_passed = False
                
        return all_passed
        
    def _check_docker(self) -> Tuple[bool, str]:
        """Check if Docker is running."""
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return True, "Docker is running"
            return False, f"Docker not responding (exit code: {result.returncode})"
        except Exception as e:
            return False, f"Docker check failed: {e}"
            
    def _check_docker_compose(self) -> Tuple[bool, str]:
        """Check if Docker Compose is available."""
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return True, f"Docker Compose available"
            return False, "Docker Compose not found"
        except Exception as e:
            return False, f"Docker Compose check failed: {e}"
            
    def _check_api_keys(self) -> Tuple[bool, str]:
        """Check if required API keys are set."""
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and api_key.startswith("sk-"):
            return True, "ANTHROPIC_API_KEY is set"
        return False, "ANTHROPIC_API_KEY not set or invalid"
        
    def _check_redis(self) -> Tuple[bool, str]:
        """Check if Redis is accessible."""
        try:
            # Try to ping Redis through Docker
            result = subprocess.run(
                ["docker", "compose", "exec", "-T", "redis", "redis-cli", "PING"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and "PONG" in result.stdout:
                return True, "Redis is responding"
            return False, "Redis not responding"
        except Exception as e:
            return False, f"Redis check failed: {e}"
            
    def _check_registry(self) -> Tuple[bool, str]:
        """Check if Agent Registry is accessible."""
        try:
            import requests
            response = requests.get("http://localhost:8090/agents", timeout=5)
            if response.status_code == 200:
                data = response.json()
                agent_count = len(data.get("agents", []))
                return True, f"Registry online with {agent_count} agents"
            return False, f"Registry returned status {response.status_code}"
        except Exception as e:
            return False, f"Registry not accessible: {e}"
            
    def run_test_module(self, module_name: str, timeout: int = 300) -> bool:
        """Run a single test module with progress reporting."""
        self.section(f"Running Test: {module_name}")
        
        test_path = f"tests/e2e_advanced/{module_name}.py"
        if not Path(test_path).exists():
            test_path = f"tests/e2e_advanced/test_{module_name}.py"
            
        cmd = [
            "python", "-m", "pytest",
            test_path,
            "-v", "-s",
            "--tb=short",
            "--color=yes",
            "-o", "log_cli=true",
            "-o", "log_cli_level=INFO"
        ]
        
        self.log(f"Command: {' '.join(cmd)}", "INFO")
        self.log(f"Timeout: {timeout} seconds", "INFO")
        
        start_time = time.time()
        last_output_time = start_time
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            output_lines = []
            while True:
                # Check for timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    self.log(f"TEST TIMEOUT after {elapsed:.1f} seconds!", "ERROR")
                    process.kill()
                    return False
                    
                # Read output with timeout
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                    
                if line:
                    line = line.rstrip()
                    output_lines.append(line)
                    
                    # Print progress indicators
                    if "PASSED" in line:
                        self.log(line, "SUCCESS", "TEST")
                    elif "FAILED" in line or "ERROR" in line:
                        self.log(line, "ERROR", "TEST")
                    elif "test_" in line:
                        self.log(line, "PROGRESS", "TEST")
                    elif self.verbose:
                        print(f"  {line}")
                        
                    last_output_time = time.time()
                    
                # Check for hanging (no output for 30 seconds)
                if time.time() - last_output_time > 30:
                    self.log("Test appears to be hanging (no output for 30s)", "WARNING")
                    
            # Get exit code
            return_code = process.poll()
            duration = time.time() - start_time
            
            # Store results
            self.test_results[module_name] = {
                "success": return_code == 0,
                "duration": duration,
                "return_code": return_code,
                "output": "\n".join(output_lines[-100:])  # Keep last 100 lines
            }
            
            if return_code == 0:
                self.log(f"Test completed successfully in {duration:.1f}s", "SUCCESS")
                return True
            else:
                self.log(f"Test failed with exit code {return_code} after {duration:.1f}s", "ERROR")
                return False
                
        except Exception as e:
            self.log(f"Test execution failed: {e}", "ERROR")
            return False
            
    def run_smoke_tests(self) -> bool:
        """Run quick smoke tests."""
        self.section("Smoke Tests")
        
        smoke_tests = [
            ("foundation_smoke", 60),
        ]
        
        all_passed = True
        for test_name, timeout in smoke_tests:
            if not self.run_test_module(test_name, timeout):
                all_passed = False
                
        return all_passed
        
    def run_advanced_tests(self) -> bool:
        """Run comprehensive E2E tests with appropriate timeouts."""
        self.section("Advanced E2E Tests")
        
        # Test modules with expected timeouts
        test_suite = [
            ("foundation_long_run", 600),      # 10 minutes
            ("context_engine_stress", 900),    # 15 minutes
            ("prp_autonomous", 1200),          # 20 minutes
            ("fleet_management", 600),         # 10 minutes
            ("workspace_integrity", 900),      # 15 minutes
            ("observability", 900),            # 15 minutes
        ]
        
        all_passed = True
        for test_name, timeout in test_suite:
            if not self.run_test_module(test_name, timeout):
                all_passed = False
                if not self.continue_on_failure():
                    break
                    
        return all_passed
        
    def continue_on_failure(self) -> bool:
        """Ask if we should continue after a failure."""
        self.log("Continue with remaining tests? (y/n)", "WARNING")
        # For AI, always continue to get full results
        return True
        
    def generate_summary(self):
        """Generate a clear summary of test results."""
        self.section("Test Execution Summary")
        
        total_duration = time.time() - self.start_time
        
        passed = sum(1 for r in self.test_results.values() if r["success"])
        failed = len(self.test_results) - passed
        
        print(f"\n{BOLD}RESULTS:{RESET}")
        print(f"  {GREEN}✅ Passed: {passed}{RESET}")
        print(f"  {RED}❌ Failed: {failed}{RESET}")
        print(f"  ⏱️  Total Duration: {total_duration:.1f} seconds")
        
        print(f"\n{BOLD}DETAILS:{RESET}")
        for test_name, result in self.test_results.items():
            status = "✅" if result["success"] else "❌"
            duration = result["duration"]
            print(f"  {status} {test_name}: {duration:.1f}s")
            
        # Write results to JSON for easy parsing
        results_file = Path("test_results.json")
        with results_file.open("w") as f:
            json.dump({
                "summary": {
                    "passed": passed,
                    "failed": failed,
                    "total_duration": total_duration,
                    "timestamp": datetime.now().isoformat()
                },
                "tests": self.test_results
            }, f, indent=2)
            
        self.log(f"Results saved to {results_file}", "INFO")
        
        # Exit with appropriate code
        sys.exit(0 if failed == 0 else 1)
        

def main():
    """Main entry point for AI-friendly test execution."""
    runner = AITestRunner(verbose=True)
    
    # ASCII art header for clear start indication
    print("""
╔══════════════════════════════════════════════════════════════════════════╗
║                    QUADRACODE AI TEST RUNNER                              ║
║                  Optimized for Automated Execution                        ║
╚══════════════════════════════════════════════════════════════════════════╝
    """)
    
    # Check prerequisites
    if not runner.check_prerequisites():
        runner.log("Prerequisites check failed! Fix issues above and retry.", "ERROR")
        sys.exit(1)
        
    # Determine test mode
    mode = os.environ.get("TEST_MODE", "smoke").lower()
    
    if mode == "smoke":
        runner.log("Running SMOKE tests only (fast)", "INFO")
        runner.run_smoke_tests()
    elif mode == "full":
        runner.log("Running FULL test suite (60-90 minutes)", "WARNING")
        runner.run_smoke_tests()
        runner.run_advanced_tests()
    else:
        runner.log(f"Unknown TEST_MODE: {mode}. Use 'smoke' or 'full'", "ERROR")
        sys.exit(1)
        
    # Generate summary
    runner.generate_summary()
    

if __name__ == "__main__":
    main()
