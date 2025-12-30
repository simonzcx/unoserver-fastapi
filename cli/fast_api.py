import uuid
import os
import re
import tempfile
import asyncio
import uvicorn
import click
import zipfile
from pathlib import Path
import glob
from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse
from starlette.background import BackgroundTask
from typing import List, Optional
from loguru import logger
from base64 import b64encode
import subprocess
import asyncio as async_io

from mineru.cli.common import aio_do_parse, read_fn, pdf_suffixes, image_suffixes
from mineru.utils.cli_parser import arg_parse
from mineru.utils.guess_suffix_or_lang import guess_suffix_by_path
from mineru.version import __version__

# 并发控制器
_request_semaphore: Optional[asyncio.Semaphore] = None

# 并发控制依赖函数
async def limit_concurrency():
    if _request_semaphore is not None:
        if _request_semaphore.locked():
            raise HTTPException(
                status_code=503,
                detail=f"Server is at maximum capacity: {os.getenv('MINERU_API_MAX_CONCURRENT_REQUESTS', 'unset')}. Please try again later."
            )
        async with _request_semaphore:
            yield
    else:
        yield

def create_app():
    # By default, the OpenAPI documentation endpoints (openapi_url, docs_url, redoc_url) are enabled.
    # To disable the FastAPI docs and schema endpoints, set the environment variable MINERU_API_ENABLE_FASTAPI_DOCS=0.
    enable_docs = str(os.getenv("MINERU_API_ENABLE_FASTAPI_DOCS", "1")).lower() in ("1", "true", "yes")
    app = FastAPI(
        openapi_url="/openapi.json" if enable_docs else None,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
    )

    # 初始化并发控制器：从环境变量MINERU_API_MAX_CONCURRENT_REQUESTS读取
    global _request_semaphore
    try:
        max_concurrent_requests = int(os.getenv("MINERU_API_MAX_CONCURRENT_REQUESTS", "0"))
    except ValueError:
        max_concurrent_requests = 0

    if max_concurrent_requests > 0:
        _request_semaphore = asyncio.Semaphore(max_concurrent_requests)
        logger.info(f"Request concurrency limited to {max_concurrent_requests}")

    app.add_middleware(GZipMiddleware, minimum_size=1000)
    return app

app = create_app()


def sanitize_filename(filename: str) -> str:
    """
    格式化压缩文件的文件名
    移除路径遍历字符, 保留 Unicode 字母、数字、._-
    禁止隐藏文件
    """
    sanitized = re.sub(r'[/\\\.]{2,}|[/\\]', '', filename)
    sanitized = re.sub(r'[^\w.-]', '_', sanitized, flags=re.UNICODE)
    if sanitized.startswith('.'):
        sanitized = '_' + sanitized[1:]
    return sanitized or 'unnamed'

def cleanup_file(file_path: str) -> None:
    """清理临时 zip 文件"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning(f"fail clean file {file_path}: {e}")

def encode_image(image_path: str) -> str:
    """Encode image using base64"""
    with open(image_path, "rb") as f:
        return b64encode(f.read()).decode()


def get_infer_result(file_suffix_identifier: str, pdf_name: str, parse_dir: str) -> Optional[str]:
    """从结果文件中读取推理结果"""
    result_file_path = os.path.join(parse_dir, f"{pdf_name}{file_suffix_identifier}")
    if os.path.exists(result_file_path):
        with open(result_file_path, "r", encoding="utf-8") as fp:
            return fp.read()
    return None


# [新增] Word文件扩展名
word_suffixes = ['doc', 'docx', 'rtf', 'odt', 'txt', 'html', 'htm']
# 支持的文档类型
supported_suffixes = pdf_suffixes + image_suffixes + word_suffixes


# [新增] Word转PDF函数 - 复用unoserver.py中的转换逻辑
async def convert_word_to_pdf(input_path: str, output_path: str):
    """
    使用unoconv将Word文档转换为PDF
    """
    logger.info(f"Converting {input_path} to {output_path}")

    # 设置环境变量以支持中文字符
    env = os.environ.copy()
    env['LANG'] = 'zh_CN.UTF-8'
    env['LC_ALL'] = 'zh_CN.UTF-8'

    try:
        # 尝试使用unoserver转换
        logger.info("Attempting conversion with unoserver")
        convert_cmd = ["unoconvert", "--host-location", "remote", "--convert-to", "pdf", input_path, output_path]
        process = await async_io.create_subprocess_exec(
            *convert_cmd,
            stdout=async_io.subprocess.PIPE,
            stderr=async_io.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.warning(f"Unoserver conversion attempt failed (return code: {process.returncode}), trying LibreOffice directly")
            # 如果unoserver失败，回退到LibreOffice
            lo_cmd = [
                "libreoffice", "--headless", "--convert-to", "pdf",
                "--outdir", os.path.dirname(output_path), input_path,
                "--norestore", "--nofirststartwizard", "--nologo", "--nolockcheck"
            ]
            lo_process = await async_io.create_subprocess_exec(
                *lo_cmd,
                stdout=async_io.subprocess.PIPE,
                stderr=async_io.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await lo_process.communicate()

            if lo_process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='replace')
                logger.error(f"LibreOffice conversion failed: {error_msg}")
                raise Exception(f"LibreOffice conversion failed: {error_msg}")
        else:
            logger.info("Unoserver conversion successful")
    except Exception as e:
        logger.error(f"Conversion error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")

    # 验证输出文件是否存在且非空
    if not os.path.exists(output_path):
        raise Exception("Conversion failed: Output file was not created")

    if os.path.getsize(output_path) == 0:
        raise Exception("Conversion failed: Output file is empty")


@app.post(path="/file_parse", dependencies=[Depends(limit_concurrency)])
async def parse_pdf(
        files: List[UploadFile] = File(..., description="Upload pdf, doc, docx, image files for parsing"),
        output_dir: str = Form("./output", description="Output local directory"),
        lang_list: List[str] = Form(
            ["ch"],
            description="""(Adapted only for pipeline backend)Input the languages in the pdf to improve OCR accuracy.
Options: ch, ch_server, ch_lite, en, korean, japan, chinese_cht, ta, te, ka, th, el, latin, arabic, east_slavic, cyrillic, devanagari.
"""
        ),
        backend: str = Form(
            "pipeline",
            description="""The backend for parsing:
- pipeline: More general
- vlm-transformers: More general, but slower
- vlm-mlx-engine: Faster than transformers (need apple silicon and macOS 13.5+)
- vlm-vllm-async-engine: Faster (vllm-engine, need vllm installed)
- vlm-lmdeploy-engine: Faster (lmdeploy-engine, need lmdeploy installed)
- vlm-http-client: Faster (client suitable for openai-compatible servers)"""
        ),
        parse_method: str = Form(
            "auto",
            description="""(Adapted only for pipeline backend)The method for parsing PDF:
- auto: Automatically determine the method based on the file type
- txt: Use text extraction method
- ocr: Use OCR method for image-based PDFs
"""
        ),
        formula_enable: bool = Form(True, description="Enable formula parsing."),
        table_enable: bool = Form(True, description="Enable table parsing."),
        server_url: Optional[str] = Form(
            None,
            description="(Adapted only for vlm-http-client backend)openai compatible server url, e.g., http://127.0.0.1:30000"
        ),
        return_md: bool = Form(True, description="Return markdown content in response"),
        return_middle_json: bool = Form(False, description="Return middle JSON in response"),
        return_model_output: bool = Form(False, description="Return model output JSON in response"),
        return_content_list: bool = Form(False, description="Return content list JSON in response"),
        return_images: bool = Form(False, description="Return extracted images in response"),
        response_format_zip: bool = Form(False, description="Return results as a ZIP file instead of JSON"),
        start_page_id: int = Form(0, description="The starting page for PDF parsing, beginning from 0"),
        end_page_id: int = Form(99999, description="The ending page for PDF parsing, beginning from 0"),
):

    # 获取命令行配置参数
    config = getattr(app.state, "config", {})

    try:
        # 创建唯一的输出目录
        unique_dir = os.path.join(output_dir, str(uuid.uuid4()))
        os.makedirs(unique_dir, exist_ok=True)

        # 处理上传的文件
        pdf_file_names = []
        pdf_bytes_list = []

        for file in files:
            content = await file.read()
            file_path = Path(file.filename)

            # 创建临时文件
            temp_path = Path(unique_dir) / file_path.name
            with open(temp_path, "wb") as f:
                f.write(content)

            # [修改] 检查文件扩展名 - 使用文件扩展名而非guess_suffix_by_path
            file_suffix = file_path.suffix.lower().lstrip('.')

            # [修改] 支持PDF和图像文件
            if file_suffix in pdf_suffixes + image_suffixes:
                try:
                    pdf_bytes = read_fn(temp_path)
                    pdf_bytes_list.append(pdf_bytes)
                    pdf_file_names.append(file_path.stem)
                    os.remove(temp_path)  # 删除临时文件
                except Exception as e:
                    return JSONResponse(
                        status_code=400,
                        content={"error": f"Failed to load file: {str(e)}"}
                    )
            # [新增] 处理Word文档和其他支持的文档格式
            elif file_suffix in word_suffixes:
                try:
                    # 转换Word文档为PDF
                    pdf_path = temp_path.with_suffix('.pdf')
                    await convert_word_to_pdf(str(temp_path), str(pdf_path))

                    # 读取转换后的PDF
                    pdf_bytes = read_fn(pdf_path)
                    pdf_bytes_list.append(pdf_bytes)
                    pdf_file_names.append(file_path.stem)

                    # 清理临时文件
                    os.remove(temp_path)  # 删除原始Word文件
                    os.remove(pdf_path)   # 删除临时PDF文件
                except Exception as e:
                    logger.error(f"Failed to convert Word file to PDF: {str(e)}")
                    return JSONResponse(
                        status_code=400,
                        content={"error": f"Failed to convert Word file to PDF: {str(e)}"}
                    )
            else:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Unsupported file type: {file_suffix}"}
                )


        # 设置语言列表，确保与文件数量一致
        actual_lang_list = lang_list
        if len(actual_lang_list) != len(pdf_file_names):
            # 如果语言列表长度不匹配，使用第一个语言或默认"ch"
            actual_lang_list = [actual_lang_list[0] if actual_lang_list else "ch"] * len(pdf_file_names)

        # 调用异步处理函数
        await aio_do_parse(
            output_dir=unique_dir,
            pdf_file_names=pdf_file_names,
            pdf_bytes_list=pdf_bytes_list,
            p_lang_list=actual_lang_list,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            server_url=server_url,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=return_md,
            f_dump_middle_json=return_middle_json,
            f_dump_model_output=return_model_output,
            f_dump_orig_pdf=False,
            f_dump_content_list=return_content_list,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
            **config
        )

        # 根据 response_format_zip 决定返回类型
        if response_format_zip:
            zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="mineru_results_")
            os.close(zip_fd)
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for pdf_name in pdf_file_names:
                    safe_pdf_name = sanitize_filename(pdf_name)
                    if backend.startswith("pipeline"):
                        parse_dir = os.path.join(unique_dir, pdf_name, parse_method)
                    else:
                        parse_dir = os.path.join(unique_dir, pdf_name, "vlm")

                    if not os.path.exists(parse_dir):
                        continue

                    # 写入文本类结果
                    if return_md:
                        path = os.path.join(parse_dir, f"{pdf_name}.md")
                        if os.path.exists(path):
                            zf.write(path, arcname=os.path.join(safe_pdf_name, f"{safe_pdf_name}.md"))

                    if return_middle_json:
                        path = os.path.join(parse_dir, f"{pdf_name}_middle.json")
                        if os.path.exists(path):
                            zf.write(path, arcname=os.path.join(safe_pdf_name, f"{safe_pdf_name}_middle.json"))

                    if return_model_output:
                        path = os.path.join(parse_dir, f"{pdf_name}_model.json")
                        if os.path.exists(path):
                            zf.write(path, arcname=os.path.join(safe_pdf_name, os.path.basename(path)))

                    if return_content_list:
                        path = os.path.join(parse_dir, f"{pdf_name}_content_list.json")
                        if os.path.exists(path):
                            zf.write(path, arcname=os.path.join(safe_pdf_name, f"{safe_pdf_name}_content_list.json"))

                    # 写入图片
                    if return_images:
                        images_dir = os.path.join(parse_dir, "images")
                        image_paths = glob.glob(os.path.join(glob.escape(images_dir), "*.jpg"))
                        for image_path in image_paths:
                            zf.write(image_path, arcname=os.path.join(safe_pdf_name, "images", os.path.basename(image_path)))

            return FileResponse(
                path=zip_path,
                media_type="application/zip",
                filename="results.zip",
                background=BackgroundTask(cleanup_file, zip_path)
            )
        else:
            # 构建 JSON 结果
            result_dict = {}
            for pdf_name in pdf_file_names:
                result_dict[pdf_name] = {}
                data = result_dict[pdf_name]

                if backend.startswith("pipeline"):
                    parse_dir = os.path.join(unique_dir, pdf_name, parse_method)
                else:
                    parse_dir = os.path.join(unique_dir, pdf_name, "vlm")

                if os.path.exists(parse_dir):
                    if return_md:
                        data["md_content"] = get_infer_result(".md", pdf_name, parse_dir)
                    if return_middle_json:
                        data["middle_json"] = get_infer_result("_middle.json", pdf_name, parse_dir)
                    if return_model_output:
                        data["model_output"] = get_infer_result("_model.json", pdf_name, parse_dir)
                    if return_content_list:
                        data["content_list"] = get_infer_result("_content_list.json", pdf_name, parse_dir)
                    if return_images:
                        images_dir = os.path.join(parse_dir, "images")
                        safe_pattern = os.path.join(glob.escape(images_dir), "*.jpg")
                        image_paths = glob.glob(safe_pattern)
                        data["images"] = {
                            os.path.basename(
                                image_path
                            ): f"data:image/jpeg;base64,{encode_image(image_path)}"
                            for image_path in image_paths
                        }

            return JSONResponse(
                status_code=200,
                content={
                    "backend": backend,
                    "version": __version__,
                    "results": result_dict
                }
            )
    except Exception as e:
        logger.exception(e)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to process file: {str(e)}"}
        )


@click.command(context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.pass_context
@click.option('--host', default='127.0.0.1', help='Server host (default: 127.0.0.1)')
@click.option('--port', default=8000, type=int, help='Server port (default: 8000)')
@click.option('--reload', is_flag=True, help='Enable auto-reload (development mode)')
def main(ctx, host, port, reload, **kwargs):

    kwargs.update(arg_parse(ctx))

    # 将配置参数存储到应用状态中
    app.state.config = kwargs

    # 将 CLI 的并发参数同步到环境变量，确保 uvicorn 重载子进程可见
    try:
        mcr = int(kwargs.get("mineru_api_max_concurrent_requests", 0) or 0)
    except ValueError:
        mcr = 0
    os.environ["MINERU_API_MAX_CONCURRENT_REQUESTS"] = str(mcr)

    """启动MinerU FastAPI服务器的命令行入口"""
    print(f"Start MinerU FastAPI Service: http://{host}:{port}")
    print(f"API documentation: http://{host}:{port}/docs")

    uvicorn.run(
        "mineru.cli.fast_api:app",
        host=host,
        port=port,
        reload=reload
    )


if __name__ == "__main__":
    main()