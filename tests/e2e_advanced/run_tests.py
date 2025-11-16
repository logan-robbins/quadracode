#!/usr/bin/env python
"""Run E2E test modules individually for debugging.

This script allows running the full E2E test modules one at a time
with clear output and debugging support.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# Test module definitions in logical order
TEST_MODULES = [
    {
        "name": "foundation_smoke",
        "module": "test_foundation_smoke.py",
        "description": "Infrastructure validation (no LLM calls)",
        "duration": "2-5 min",
        "marks": [],
    },
    {
        "name": "foundation_long_run",
        "module": "test_foundation_long_run.py", 
        "description": "Sustained message flows and routing",
        "duration": "5-10 min",
        "marks": ["e2e_advanced"],
    },
    {
        "name": "context_engine_stress",
        "module": "test_context_engine_stress.py",
        "description": "Context engineering under load",
        "duration": "10-15 min",
        "marks": ["e2e_advanced"],
    },
    {
        "name": "prp_autonomous",
        "module": "test_prp_autonomous.py",
        "description": "HumanClone rejection cycles",
        "duration": "15-20 min",
        "marks": ["e2e_advanced"],
    },
    {
        "name": "fleet_management",
        "module": "test_fleet_management.py",
        "description": "Dynamic agent lifecycle",
        "duration": "5-10 min",
        "marks": ["e2e_advanced"],
    },
    {
        "name": "workspace_integrity", 
        "module": "test_workspace_integrity.py",
        "description": "Multi-workspace isolation",
        "duration": "10-15 min",
        "marks": ["e2e_advanced"],
    },
    {
        "name": "observability",
        "module": "test_observability.py",
        "description": "Time-travel logs and metrics",
        "duration": "10-15 min",
        "marks": ["e2e_advanced"],
    },
]


def print_separator(char="=", width=80):
    """Print a separator line."""
    print(char * width)


def list_modules():
    """List all available test modules."""
    print_separator()
    print("AVAILABLE E2E TEST MODULES")
    print_separator()
    
    for i, test in enumerate(TEST_MODULES, 1):
        marks = f" [{', '.join(test['marks'])}]" if test["marks"] else ""
        print(f"{i}. {test['name']}{marks}")
        print(f"   Module: {test['module']}")
        print(f"   Description: {test['description']}")
        print(f"   Duration: {test['duration']}")
        print()


def run_test_module(
    test_name: str,
    verbose: bool = False,
    capture: bool = True,
    log_dir: Optional[Path] = None,
) -> bool:
    """Run a single test module.
    
    Args:
        test_name: Name of the test module to run
        verbose: Enable verbose pytest output
        capture: Capture output to file
        log_dir: Directory for logs
        
    Returns:
        True if test passed, False otherwise
    """
    # Find test module
    test_info = None
    for test in TEST_MODULES:
        if test["name"] == test_name:
            test_info = test
            break
    
    if not test_info:
        print(f"Error: Test module '{test_name}' not found")
        return False
    
    # Create log directory
    if log_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = Path("logs") / f"run_{test_name}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Build pytest command
    cmd = ["uv", "run", "pytest", test_info["module"]]
    
    # Add marks if any
    if test_info["marks"]:
        for mark in test_info["marks"]:
            cmd.extend(["-m", mark])
    
    # Add verbose flag
    if verbose:
        cmd.append("-vv")
        cmd.append("--log-cli-level=DEBUG")
    else:
        cmd.append("-v")
        cmd.append("--log-cli-level=INFO")
    
    # Add output capture
    if capture:
        cmd.extend([
            "--tb=short",
            f"--junit-xml={log_dir}/junit.xml",
            f"--html={log_dir}/report.html",
            "--self-contained-html",
        ])
    
    # Print test header
    print_separator("=")
    print(f"RUNNING: {test_info['name']}")
    print(f"Module: {test_info['module']}")
    print(f"Description: {test_info['description']}")
    print(f"Expected Duration: {test_info['duration']}")
    print(f"Log Directory: {log_dir}")
    print_separator("-")
    
    # Run the test
    start_time = time.time()
    
    if capture:
        # Capture output to file
        output_file = log_dir / "output.log"
        with open(output_file, "w") as f:
            print(f"Command: {' '.join(cmd)}", file=f)
            print(f"Started: {datetime.now()}", file=f)
            print("-" * 80, file=f)
            
            # Run with output capture
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            
            # Write output to file
            f.write(result.stdout)
            
            # Also print to console if not too long
            if len(result.stdout.splitlines()) < 100 or verbose:
                print(result.stdout)
            else:
                # Print summary
                lines = result.stdout.splitlines()
                print("\n".join(lines[:20]))
                print(f"\n... ({len(lines) - 40} lines omitted) ...\n")
                print("\n".join(lines[-20:]))
                print(f"\nFull output saved to: {output_file}")
    else:
        # Run without capture (direct to console)
        result = subprocess.run(cmd)
    
    duration = time.time() - start_time
    
    # Print results
    print_separator("-")
    print(f"RESULT: {'PASSED' if result.returncode == 0 else 'FAILED'}")
    print(f"Duration: {duration:.2f}s")
    if capture:
        print(f"Logs: {log_dir}")
    print_separator("=")
    print()
    
    # Save summary
    if capture:
        summary = {
            "test_name": test_name,
            "module": test_info["module"],
            "started": start_time,
            "duration_seconds": duration,
            "exit_code": result.returncode,
            "passed": result.returncode == 0,
        }
        with open(log_dir / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)
    
    return result.returncode == 0


def run_all_modules(
    verbose: bool = False,
    stop_on_failure: bool = False,
    capture: bool = True,
) -> int:
    """Run all test modules in sequence.
    
    Args:
        verbose: Enable verbose output
        stop_on_failure: Stop at first failure
        capture: Capture output to files
        
    Returns:
        Number of failed modules
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = Path("logs") / f"session_{timestamp}"
    session_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    failed_count = 0
    
    print_separator("=")
    print("E2E TEST SESSION")
    print(f"Timestamp: {timestamp}")
    print(f"Session Directory: {session_dir}")
    print(f"Total Modules: {len(TEST_MODULES)}")
    print_separator("=")
    print()
    
    for i, test in enumerate(TEST_MODULES, 1):
        print(f"[{i}/{len(TEST_MODULES)}] Running {test['name']}...")
        
        log_dir = session_dir / test["name"]
        passed = run_test_module(
            test["name"],
            verbose=verbose,
            capture=capture,
            log_dir=log_dir,
        )
        
        results.append({
            "name": test["name"],
            "passed": passed,
        })
        
        if not passed:
            failed_count += 1
            if stop_on_failure:
                print(f"Stopping due to failure in {test['name']}")
                break
    
    # Print summary
    print_separator("=")
    print("SESSION SUMMARY")
    print_separator("-")
    
    for result in results:
        status = "✓ PASSED" if result["passed"] else "✗ FAILED"
        print(f"{status}: {result['name']}")
    
    print_separator("-")
    print(f"Total: {len(results)}")
    print(f"Passed: {len([r for r in results if r['passed']])}")
    print(f"Failed: {failed_count}")
    print(f"Session logs: {session_dir}")
    print_separator("=")
    
    return failed_count


def main():
    """Main entry point for the test runner."""
    parser = argparse.ArgumentParser(
        description="Run E2E test modules individually or in sequence"
    )
    
    parser.add_argument(
        "module",
        nargs="?",
        help="Name of specific test module to run (or 'all' for all modules)",
    )
    
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available test modules",
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    
    parser.add_argument(
        "--no-capture",
        action="store_true",
        help="Don't capture output to files",
    )
    
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="Stop at first failure when running all modules",
    )
    
    args = parser.parse_args()
    
    # List modules if requested
    if args.list:
        list_modules()
        return 0
    
    # If no module specified, show help
    if not args.module:
        list_modules()
        print("\nUsage:")
        print("  Run specific module:  python run_tests.py <module_name>")
        print("  Run all modules:      python run_tests.py all")
        print("  List modules:         python run_tests.py --list")
        return 0
    
    # Run all modules
    if args.module.lower() == "all":
        failed_count = run_all_modules(
            verbose=args.verbose,
            stop_on_failure=args.stop_on_failure,
            capture=not args.no_capture,
        )
        return 1 if failed_count > 0 else 0
    
    # Run specific module
    passed = run_test_module(
        args.module,
        verbose=args.verbose,
        capture=not args.no_capture,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
