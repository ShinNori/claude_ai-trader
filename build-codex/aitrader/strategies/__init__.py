from .margin_bucket_long import MarginBucketLong

def validate_strategy_name(name):
    if name != 'margin_bucket_long':
        raise ValueError(f'Unknown strategy: {name}')
    return name


def get_strategy(name):
    validate_strategy_name(name)
    return MarginBucketLong()
