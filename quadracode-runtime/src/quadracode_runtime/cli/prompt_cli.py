#!/usr/bin/env python3
"""
Command-line interface for managing prompt templates.

This CLI tool provides commands for viewing, editing, and testing prompt templates
used by the Quadracode context engine.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from quadracode_runtime.config import PromptTemplates
from quadracode_runtime.config.prompt_manager import PromptManager


def cmd_list(args):
    """List all available prompt templates."""
    manager = PromptManager()
    templates = manager.export_to_dict()
    
    print("Available Prompt Templates:")
    print("=" * 50)
    
    for category, items in templates.items():
        print(f"\n{category.upper()}:")
        if isinstance(items, dict):
            for key in items.keys():
                print(f"  - {key}")
        elif isinstance(items, str):
            print(f"  {category}: {items[:60]}...")


def cmd_show(args):
    """Show a specific prompt template."""
    manager = PromptManager()
    templates = manager.get_templates()
    
    if hasattr(templates, args.template):
        value = getattr(templates, args.template)
        print(f"Template: {args.template}")
        print("=" * 50)
        if isinstance(value, str):
            print(value)
        else:
            print(json.dumps(value, indent=2))
    else:
        print(f"Error: Unknown template '{args.template}'")
        sys.exit(1)


def cmd_update(args):
    """Update a prompt template."""
    manager = PromptManager()
    
    # Read new value from file or stdin
    if args.file:
        with open(args.file, 'r') as f:
            value = f.read()
    else:
        print(f"Enter new value for {args.template} (Ctrl+D to finish):")
        value = sys.stdin.read()
    
    try:
        manager.update_template(args.template, value.strip())
        if args.save:
            manager.save_to_file()
            print(f"Updated and saved {args.template}")
        else:
            print(f"Updated {args.template} (use --save to persist)")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_export(args):
    """Export prompt templates to a file."""
    manager = PromptManager()
    
    output_path = Path(args.output)
    manager.save_to_file(output_path)
    print(f"Exported templates to {output_path}")


def cmd_import(args):
    """Import prompt templates from a file."""
    manager = PromptManager()
    
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File {input_path} does not exist")
        sys.exit(1)
    
    try:
        manager.load_from_file(input_path)
        print(f"Imported templates from {input_path}")
        
        if args.save:
            manager.save_to_file()
            print("Saved imported templates to default location")
    except Exception as e:
        print(f"Error importing templates: {e}")
        sys.exit(1)


def cmd_test(args):
    """Test a prompt template with sample variables."""
    manager = PromptManager()
    templates = manager.get_templates()
    
    # Parse variables from command line
    variables = {}
    if args.vars:
        for var in args.vars:
            key, value = var.split('=', 1)
            variables[key] = value
    
    # Get the formatted prompt
    try:
        if args.domain or args.pressure:
            context_ratio = float(args.pressure) if args.pressure else None
            prompt = manager.get_effective_prompt(
                args.template,
                context_ratio=context_ratio,
                domain=args.domain,
                **variables
            )
        else:
            prompt = templates.get_prompt(args.template, **variables)
        
        print("Formatted Prompt:")
        print("=" * 50)
        print(prompt)
        
        # Show token estimate
        token_estimate = len(prompt.split())
        print(f"\nEstimated tokens: ~{token_estimate}")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_validate(args):
    """Validate all prompt templates."""
    manager = PromptManager()
    
    if manager.validate_templates():
        print("✓ All templates are valid")
    else:
        print("✗ Template validation failed")
        sys.exit(1)


def cmd_reset(args):
    """Reset all templates to defaults."""
    if not args.force:
        response = input("Are you sure you want to reset all templates to defaults? (y/N): ")
        if response.lower() != 'y':
            print("Cancelled")
            return
    
    manager = PromptManager()
    manager.reset_to_defaults()
    
    if args.save:
        manager.save_to_file()
        print("Reset and saved default templates")
    else:
        print("Reset to defaults (use --save to persist)")


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Manage Quadracode prompt templates"
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # List command
    parser_list = subparsers.add_parser('list', help='List all templates')
    
    # Show command
    parser_show = subparsers.add_parser('show', help='Show a specific template')
    parser_show.add_argument('template', help='Template name to show')
    
    # Update command
    parser_update = subparsers.add_parser('update', help='Update a template')
    parser_update.add_argument('template', help='Template name to update')
    parser_update.add_argument('--file', '-f', help='Read new value from file')
    parser_update.add_argument('--save', '-s', action='store_true',
                              help='Save changes to configuration file')
    
    # Export command
    parser_export = subparsers.add_parser('export', help='Export templates to file')
    parser_export.add_argument('output', help='Output file path')
    
    # Import command
    parser_import = subparsers.add_parser('import', help='Import templates from file')
    parser_import.add_argument('input', help='Input file path')
    parser_import.add_argument('--save', '-s', action='store_true',
                              help='Save imported templates')
    
    # Test command
    parser_test = subparsers.add_parser('test', help='Test a template with variables')
    parser_test.add_argument('template', help='Template name to test')
    parser_test.add_argument('--vars', '-v', nargs='*',
                           help='Variables in key=value format')
    parser_test.add_argument('--domain', '-d',
                           help='Domain for domain-specific enhancements')
    parser_test.add_argument('--pressure', '-p',
                           help='Context pressure (0.0-1.0) for pressure modifiers')
    
    # Validate command
    parser_validate = subparsers.add_parser('validate', help='Validate all templates')
    
    # Reset command
    parser_reset = subparsers.add_parser('reset', help='Reset to default templates')
    parser_reset.add_argument('--force', '-f', action='store_true',
                            help='Skip confirmation')
    parser_reset.add_argument('--save', '-s', action='store_true',
                            help='Save reset templates')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Map commands to functions
    commands = {
        'list': cmd_list,
        'show': cmd_show,
        'update': cmd_update,
        'export': cmd_export,
        'import': cmd_import,
        'test': cmd_test,
        'validate': cmd_validate,
        'reset': cmd_reset,
    }
    
    command_func = commands.get(args.command)
    if command_func:
        command_func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
