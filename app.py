from flask import Flask, request, send_file, abort, make_response
from PIL import Image
import io
import requests
from datetime import datetime
import time

app = Flask(__name__)
MAX_DIM = 2000

def fetch_image(url: str) -> Image.Image:
    try:
        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        return Image.open(response.raw)
    except Exception:
        abort(400, description="Failed to fetch image")

@app.route("/")
def resize():
    # OPTIONS 请求处理（CORS 预检）
    if request.method == "OPTIONS":
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'

        return response    
    # 获取客户端缓存头
    if_none_match = request.headers.get("If-None-Match", "")
    if_modified_since = request.headers.get("If-Modified-Since", "")
     # 判断是否命中缓存
    if if_none_match :
        response = make_response('', 304)
        response.headers['ETag'] = if_none_match
        response.headers['Last-Modified'] = if_modified_since
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response   
        
    image_url = request.args.get("url")
    width = request.args.get("w", type=int)
    height = request.args.get("h", type=int)
    output_format = request.args.get("output", "jpg").lower()
    quality = request.args.get("quality", type=int) or 85

    if not image_url:
        return f"Hello, 世界！这是一个简单的 Node.js Web 服务。"

    # 获取原始图片信息和Last-Modified
    try:
        response = requests.head(image_url, timeout=10)
        response.raise_for_status()
        last_modified = response.headers.get('Last-Modified')
        if not last_modified:
            # 如果没有Last-Modified头，使用当前时间
            last_modified = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())
    except Exception:
        last_modified = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())

    img = fetch_image(image_url)
    orig_width, orig_height = img.size

    # 限制最大尺寸
    if (width and width > MAX_DIM): width = None
    if (height and height > MAX_DIM): height = None

    # 等比例缩放逻辑（fit 默认）
    if width and not height:
        ratio = width / orig_width
        height = int(orig_height * ratio)
    elif height and not width:
        ratio = height / orig_height
        width = int(orig_width * ratio)
    elif width and height:
        ratio_w = width / orig_width
        ratio_h = height / orig_height
        ratio = min(ratio_w, ratio_h)
        width = int(orig_width * ratio)
        height = int(orig_height * ratio)
    else:
        width, height = orig_width, orig_height

    # 如果尺寸未变，返回原图
    if width >= orig_width and height >= orig_height:
        buf = io.BytesIO()
        img.save(buf, format=output_format.upper(), quality=quality)
        buf.seek(0)
        
        # 创建响应并添加头部
        response = make_response(send_file(buf, mimetype=f"image/{output_format}"))
        response.headers['Last-Modified'] = last_modified
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        response.headers['Expires'] = 'Thu, 31 Dec 2037 23:55:55 GMT'
        response.headers['ETag'] = f'"{hash(buf.getvalue())}"'
        
        return response

    # 执行缩放
    img_resized = img.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img_resized.save(buf, format=output_format.upper(), quality=quality)
    buf.seek(0)
    
    # 创建响应并添加头部
    response = make_response(send_file(buf, mimetype=f"image/{output_format}"))
    response.headers['Last-Modified'] = last_modified
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    response.headers['ETag'] = f'"{hash(buf.getvalue())}"'
    
    return response

if __name__ == "__main__":
    app.run()
