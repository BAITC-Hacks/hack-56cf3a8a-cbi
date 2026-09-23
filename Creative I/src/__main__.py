"""Run with python -m src --help."""
import argparse
import json
from dataclasses import asdict
from datetime import date
from .contractor_matcher import Request, load_catalog, match


def main():
    parser = argparse.ArgumentParser(description='Подбор event-подрядчиков')
    parser.add_argument('--catalog', default='data/contractors.csv')
    parser.add_argument('--city', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--format', dest='event_format', required=True)
    parser.add_argument('--category', required=True)
    parser.add_argument('--budget', dest='budget_kzt', type=int, required=True)
    parser.add_argument('--hours', dest='duration_hours', type=float)
    parser.add_argument('--language')
    parser.add_argument('--offline', action='store_true')
    args = vars(parser.parse_args())
    catalog = args.pop('catalog')
    offline = args.pop('offline')
    try:
        date.fromisoformat(args['date'])
        if args['budget_kzt'] < 0 or (args['duration_hours'] is not None and args['duration_hours'] <= 0):
            raise ValueError('Бюджет должен быть >= 0, длительность > 0')
        result = match(Request(**args), load_catalog(catalog), offline=offline)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
