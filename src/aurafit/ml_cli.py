"""CLI: reproducible synthetic features, training, forecasting and optional SQL writes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .models import UserCreate, IngestBatch
from .features import build_features
from .predictive import train, forecast
from .feature_store import persist_feature_days


def load_days(path: Path):
    user=UserCreate.model_validate_json((path/'user.json').read_text(encoding='utf8'))
    batches=tuple(IngestBatch.model_validate_json(f.read_text(encoding='utf8')) for f in sorted((path/'batches').glob('batch_*.json')))
    if not batches:
        raise ValueError('no batch files found')
    return build_features(user,batches)


def main():
    ap=argparse.ArgumentParser(description='AuraFit experimental offline ML')
    ap.add_argument('command',choices=['features','train','predict'])
    ap.add_argument('--input',required=True,type=Path)
    ap.add_argument('--output',type=Path,default=Path('generated/ml'))
    ap.add_argument('--database-url',help='optional database URL for features command')
    args=ap.parse_args()
    days=load_days(args.input)
    if args.command=='features':
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'features.json').write_text(json.dumps([{'date':d.local_date.isoformat(), 'baseline':d.baseline.model_dump(mode='json'), 'assessment':d.assessment.model_dump(mode='json'),'features':d.values,'forecast_eligible':d.valid_forecast} for d in days],indent=2)+'\n')
        if args.database_url:
            print(f'Persisted {persist_feature_days(args.database_url,days)} days')
        print(f'Computed {len(days)} days at {args.output}')
    elif args.command=='train':
        print(json.dumps(train(days,args.output),indent=2))
    else:
        print(json.dumps(forecast(days,args.output).__dict__,indent=2,default=str))

if __name__=='__main__':
    main()
