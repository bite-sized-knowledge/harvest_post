import json
from db_conn import Connection
from preprocessing \
    import WoowahanProcessor, TossProcessor, MediumProcessor, KakaoProcessor, OliveYoungProcessor

def postprocess_by_blog_id(text, blog_id):
    processors = {
        1: WoowahanProcessor,
        2: TossProcessor,
        3: MediumProcessor,
        4: KakaoProcessor,
        5: OliveYoungProcessor
    }
    processor_class = processors.get(blog_id)
    if not processor_class:
        raise ValueError(f"Unsupported blog_id: {blog_id}")
    processor = processor_class(text, blog_id)
    return processor.process()


def gen_insertion_article_info_query():
    column_names = [
        'article_id', 'blog_id', 'url', 'title', 'thumbnail',
        'description', 'keywords', 'category_id', 'content', 'content_length',
        'lang', 'published_at'
    ]
    columns_str = ", ".join(column_names)

    placeholders = ", ".join(["%s" for _ in column_names])

    query = f"INSERT IGNORE INTO article_local ({columns_str}) VALUES ({placeholders})"
    return query

def lambda_handler(event, context):
    conn = Connection()


    for data in event:
        blog_id = int(data.get('blog_id'))
        text = data.get('content')

        query = gen_insertion_article_info_query()
        try:
            preprocessed = postprocess_by_blog_id(text, blog_id)
            values = (
                data.get('article_id'),
                blog_id,
                data.get('url'),
                data.get('title'),
                data.get('thumbnail'),
                data.get('description'),
                data.get('keywords') if data.get('keywords') else '',  # Handle None for keywords
                data.get('category_id', 0),
                preprocessed,
                data.get('content_length', 0),
                data.get('lang', 'ko'),
                data.get('published_at')
            )
            conn._raw_execute(query, values)


        except Exception as e:
            return {
                'statusCode' : 400,
                'msg' : str(e)
            }