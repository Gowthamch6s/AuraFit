"""CLI for synthetic generation and transactional loading."""
import argparse
import json
import os
from datetime import date
from pathlib import Path
from .synthetic import GeneratorConfig, SCENARIOS, generate
from .loader import load_directory


def main() -> None:
    parser = argparse.ArgumentParser(description='AuraFit synthetic physiological data')
    subs = parser.add_subparsers(dest='command', required=True)
    gen = subs.add_parser('generate', help='Generate a validated synthetic dataset')
    gen.add_argument('--output', type=Path, required=True)
    gen.add_argument('--seed', type=int, default=42)
    gen.add_argument('--days', type=int, default=30)
    gen.add_argument('--start-date', type=date.fromisoformat, default=date(2026, 9, 1))
    gen.add_argument('--timezone', default='America/New_York')
    gen.add_argument('--scenario', choices=SCENARIOS, default='balanced')
    gen.add_argument('--missing-rate', type=float, default=0.03)
    load = subs.add_parser('load', help='Load a generated dataset to TimescaleDB')
    load.add_argument('--input', type=Path, required=True)
    load.add_argument('--database-url', default=os.getenv('DATABASE_URL'))
    args = parser.parse_args()
    if args.command == 'generate':
        dataset = generate(GeneratorConfig(seed=args.seed, days=args.days,
            start_date=args.start_date, timezone=args.timezone,
            scenario=args.scenario, missing_rate=args.missing_rate))
        if args.output.exists() and any(args.output.iterdir()):
            parser.error('output directory must be absent or empty to avoid stale files')
        dataset.write(args.output)
        print(json.dumps(dataset.manifest, indent=2))
    else:
        if not args.database_url:
            parser.error('Provide --database-url or DATABASE_URL')
        print(json.dumps(load_directory(args.database_url, args.input), indent=2))

if __name__ == '__main__':
    main()
