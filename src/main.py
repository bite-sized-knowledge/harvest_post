import os
from db_conn import Connection
from http import HTTPStatus
from response import HTTPResponse
from preprocessing \
    import WoowahanProcessor, TossProcessor, MediumProcessor, KakaoProcessor, OliveYoungProcessor

# Pre-compile the INSERT query outside the handler
ARTICLE_TABLE = os.getenv('ARTICLE_TABLE')
COLUMN_NAMES = [
    'article_id', 'blog_id', 'url', 'title', 'thumbnail',
    'description', 'keywords', 'category_id', 'content', 'content_length',
    'lang', 'published_at'
]
COLUMNS_STR = ", ".join(COLUMN_NAMES)
PLACEHOLDERS = ", ".join(["%s" for _ in COLUMN_NAMES])
INSERT_QUERY = f"INSERT IGNORE INTO {ARTICLE_TABLE} ({COLUMNS_STR}) VALUES ({PLACEHOLDERS})"

# Define processors outside the handler
PROCESSORS = {
    1: WoowahanProcessor,
    2: TossProcessor,
    3: MediumProcessor,
    4: KakaoProcessor,
    5: OliveYoungProcessor
}

def postprocess_by_blog_id(text, blog_id):
    processor_class = PROCESSORS.get(blog_id)
    if not processor_class:
        raise ValueError(f"Unsupported blog_id: {blog_id}")
    processor = processor_class(text, blog_id)
    return processor.process()

def lambda_handler(event, context):
    conn = Connection()

    for data in event:
        blog_id = int(data.get('blog_id'))
        text = data.get('content')

        try:
            preprocessed = postprocess_by_blog_id(text, blog_id)
            values = (
                data.get('article_id'),
                blog_id,
                data.get('url'),
                data.get('title'),
                data.get('thumbnail'),
                data.get('description'),
                data.get('keywords') if data.get('keywords') else 'None',  # Handle None for keywords
                data.get('category_id', 0),
                preprocessed,
                data.get('content_length', 0),
                data.get('lang', 'ko'),
                data.get('published_at')
            )
            conn._raw_execute(INSERT_QUERY, values)

        except Exception as e:
            conn.close()
            response = HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
            return response.get_response()

    conn.close()
    response = HTTPResponse(HTTPStatus.CREATED) 
    return response.get_response()