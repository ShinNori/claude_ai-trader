from .margin_bucket_long import MarginBucketLong

def get_strategy(name):
    if name != 'margin_bucket_long':
        raise ValueError(f'Unknown strategy: {name}')
    return MarginBucketLong()
