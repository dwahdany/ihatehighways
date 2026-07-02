from app.knapsack import Item, select

BUCKET = 15


def test_free_items_selected_even_with_zero_budget():
    items = [
        Item(key=0, cost_s=-50.0, value_s=120.0),  # jammed highway: detour is faster
        Item(key=1, cost_s=0.0, value_s=10.0),
        Item(key=2, cost_s=30.0, value_s=1000.0),  # paid, no budget
    ]
    assert select(items, budget_s=0, bucket_s=BUCKET) == {0, 1}


def test_budget_respected():
    items = [
        Item(key=0, cost_s=100.0, value_s=100.0),
        Item(key=1, cost_s=200.0, value_s=190.0),
        Item(key=2, cost_s=300.0, value_s=280.0),
    ]
    chosen = select(items, budget_s=250, bucket_s=BUCKET)
    assert chosen  # something affordable exists
    assert sum(it.cost_s for it in items if it.key in chosen) <= 250


def test_picks_higher_value_combo_over_greedy_trap():
    # Greedy by value/cost ratio picks B (ratio 0.56) first, after which nothing else
    # fits and total value is 50. The optimum is A alone with value 60.
    items = [
        Item(key=0, cost_s=150.0, value_s=60.0),  # A
        Item(key=1, cost_s=90.0, value_s=50.0),  # B
        Item(key=2, cost_s=75.0, value_s=40.0),  # C
    ]
    assert select(items, budget_s=150, bucket_s=BUCKET) == {0}


def test_two_small_beat_one_big():
    items = [
        Item(key=0, cost_s=150.0, value_s=60.0),
        Item(key=1, cost_s=75.0, value_s=45.0),
        Item(key=2, cost_s=75.0, value_s=45.0),
    ]
    assert select(items, budget_s=150, bucket_s=BUCKET) == {1, 2}


def test_no_items():
    assert select([], budget_s=900, bucket_s=BUCKET) == set()
