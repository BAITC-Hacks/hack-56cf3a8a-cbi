"""Reproducible offline scenarios using the supplied hackathon CSV."""
import json
from dataclasses import asdict, replace
from .contractor_matcher import Request, load_catalog, match


def main():
    catalog = load_catalog('data/contractors.csv')
    base = Request('Алматы', '2026-10-10', 'корпоратив', 'Ведущий', 2000000)
    for label, query in [
        ('Плотная категория', base),
        ('Редкая категория', replace(base, category='Флорист')),
        ('Недостаточный бюджет', replace(base, budget_kzt=1)),
        ('Нет категории', replace(base, category='Несуществующая категория')),
        ('Другая дата', replace(base, date='2026-10-11')),
    ]:
        print(label)
        print(json.dumps(asdict(match(query, catalog, offline=True)), ensure_ascii=False, indent=2))
    orders = [[m['id'] for m in match(base, catalog, offline=True).matches] for _ in range(3)]
    assert orders[0] == orders[1] == orders[2]
    print('Порядок трёх повторов:', orders)


if __name__ == '__main__':
    main()
