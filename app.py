from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
import tempfile
import os
import asyncio
import logging
import os
import socket

# Configure logging with UTF-8 support
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 配置常量和环境变量支持 - 统一管理超时和重试参数
# 转换超时时间（秒）
CONVERSION_TIMEOUT = int(os.environ.get('CONVERSION_TIMEOUT', 300))
# 连接超时时间（秒）
CONNECTION_TIMEOUT = int(os.environ.get('CONNECTION_TIMEOUT', 10))
# 最大重试次数
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 3))
# 重试基础延迟（秒）
BASE_RETRY_DELAY = int(os.environ.get('BASE_RETRY_DELAY', 1))
# 最大重试延迟（秒）
MAX_RETRY_DELAY = int(os.environ.get('MAX_RETRY_DELAY', 10))
# 文件大小限制（字节，默认50MB）
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 50 * 1024 * 1024))

# 确保中文字体正确渲染的区域设置
def setup_locale():
    """
    设置区域环境变量以支持中文文本渲染
    """
    os.environ['LANG'] = 'zh_CN.UTF-8'
    os.environ['LC_ALL'] = 'zh_CN.UTF-8'
    os.environ['LC_CTYPE'] = 'zh_CN.UTF-8'
    logger.info("已配置区域环境变量以支持中文文本")

# Call setup function
setup_locale()

# 重试装饰器 - 实现指数退避重试逻辑
def retry_with_backoff(max_retries=MAX_RETRIES, base_delay=BASE_RETRY_DELAY, max_delay=MAX_RETRY_DELAY):
    """
    带指数退避的异步重试装饰器
    
    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            retries = 0
            delay = base_delay
            while retries < max_retries:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # 只重试可重试的错误类型
                    if isinstance(e, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
                        retries += 1
                        if retries >= max_retries:
                            logger.error(f"已达到最大重试次数 {max_retries}，操作失败: {e}")
                            raise
                        logger.warning(f"重试 {retries}/{max_retries} 次，等待 {delay} 秒后重试，错误: {e}")
                        await asyncio.sleep(delay)
                        # 指数退避算法，每次延迟翻倍，不超过最大延迟
                        delay = min(delay * 2, max_delay)
                    else:
                        # 不可重试的错误直接抛出
                        raise
        return wrapper
    return decorator

def check_unoserver_connection():
    """检查 unoserver 连接是否可用"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CONNECTION_TIMEOUT)  # 使用统一的连接超时设置
        result = sock.connect_ex(('localhost', 2003))
        sock.close()
        return result == 0
    except Exception:
        return False

# 优化的命令执行函数 - 改进错误处理和超时管理
@retry_with_backoff()
async def execute_command(cmd, env=None, timeout=CONVERSION_TIMEOUT):
    """
    执行命令并处理超时和错误
    
    Args:
        cmd: 命令列表
        env: 环境变量字典
        timeout: 超时时间（秒）
    
    Returns:
        命令执行的标准输出
    
    Raises:
        Exception: 命令执行失败时抛出异常
    """
    logger.info(f"执行命令: {' '.join(cmd)}")
    try:
        # 创建异步子进程
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            ),
            timeout=timeout
        )
        
        # 等待命令完成
        stdout, stderr = await process.communicate()
        
        # 检查返回码
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='replace')
            logger.error(f"命令执行失败，返回码: {process.returncode}，错误信息: {error_msg}")
            raise Exception(f"命令执行失败: {error_msg}")
        
        return stdout.decode('utf-8', errors='replace')
    except asyncio.TimeoutError:
        logger.error(f"命令执行超时 ({timeout} 秒): {' '.join(cmd)}")
        raise
    except Exception as e:
        logger.error(f"执行命令时发生错误: {' '.join(cmd)}，错误: {e}")
        raise

app = FastAPI(title="Word to PDF Converter", description="Convert Word documents to PDF using LibreOffice")

@app.post("/word_to_pdf", response_description="Converted PDF file")
async def convert_word_to_pdf(file: UploadFile = File(..., description="Word document to convert")):
    """
    将Word文档转换为PDF格式，支持中文字体

    Args:
        file: 上传的Word文档文件

    Returns:
        PDF文件的字节数据
    """
    logger.info(f"收到文件: {file.filename}, 内容类型: {file.content_type}")

    # 验证文件扩展名
    if not file.filename.endswith((".docx", ".doc", ".rtf", ".odt")):
        logger.warning(f"无效的文件扩展名: {file.filename}")
        raise HTTPException(status_code=400, detail="仅支持Word文档 (.docx, .doc, .rtf, .odt)")

    # 验证文件大小 (使用统一的配置常量)
    file_content = await file.read()
    if len(file_content) > MAX_FILE_SIZE:
        max_size_mb = MAX_FILE_SIZE / (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"文件过大，最大支持 {max_size_mb}MB")

    # 初始化临时文件路径变量
    input_path = None
    output_path = None
    
    try:
        # 创建临时输入文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_input:
            temp_input.write(file_content)
            input_path = temp_input.name

        # 生成输出PDF文件路径
        output_path = input_path.rsplit('.', 1)[0] + '.pdf'
        logger.info(f"转换路径 - 输入: {input_path}, 输出: {output_path}")

        # 设置中文字体支持的环境变量
        env = os.environ.copy()
        env['LANG'] = 'zh_CN.UTF-8'
        env['LC_ALL'] = 'zh_CN.UTF-8'

        # 首先尝试使用unoserver进行转换
        try:
            logger.info("尝试使用unoserver进行转换")
            convert_cmd = [
                "unoconvert",
                "--host-location", "remote",
                "--host", "localhost",
                "--port", "2003",
                "--timeout", str(CONVERSION_TIMEOUT),  # 使用统一的转换超时设置
                "--convert-to", "pdf:writer_pdf_Export",  # 使用writer_pdf_Export过滤器
                "--writer-pdf-output-resample-images", "false",  # 不重新采样图片
                "--writer-pdf-output-image-resolution", "300",  # 设置图片分辨率为300dpi
                input_path,
                output_path
            ]
            
            # 使用优化的命令执行函数，带重试机制
            await execute_command(convert_cmd, env=env, timeout=CONVERSION_TIMEOUT)
            logger.info("Unoserver转换成功")
        except Exception as e:
            # 降级到直接使用LibreOffice
            logger.warning(f"Unoserver转换失败，尝试直接使用LibreOffice: {e}")
            lo_cmd = [
                "libreoffice",
                "--headless",  # 无头模式
                "--invisible",  # 不可见模式
                "--nologo",  # 不显示logo
                "--nodefault",  # 不打开默认文档
                "--convert-to", "pdf:writer_pdf_Export",  # 使用writer_pdf_Export过滤器
                "--outdir", os.path.dirname(output_path),  # 输出目录
                "--writer-pdf-output-resample-images", "false",  # 不重新采样图片
                "--writer-pdf-output-image-resolution", "300",  # 设置图片分辨率为300dpi
                input_path  # 输入文件
            ]
            
            # 使用优化的命令执行函数，带重试机制
            await execute_command(lo_cmd, env=env, timeout=CONVERSION_TIMEOUT)
            logger.info("LibreOffice转换成功")

        # 验证输出文件是否存在且不为空
        if not os.path.exists(output_path):
            raise Exception("转换失败: 输出文件未创建")

        if os.path.getsize(output_path) == 0:
            raise Exception("转换失败: 输出文件为空")

        # 读取PDF文件
        with open(output_path, "rb") as pdf_file:
            pdf_bytes = pdf_file.read()

        logger.info(f"转换成功，PDF大小: {len(pdf_bytes)} 字节")

        # 返回PDF文件
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={os.path.basename(output_path)}",
                "X-Converted-By": "unoserver-docker-api"
            }
        )

    except asyncio.TimeoutError:
        logger.error("转换超时")
        raise HTTPException(status_code=408, detail="转换超时，请尝试较小的文件")
    except Exception as e:
        logger.error(f"转换错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"转换失败: {str(e)}")
    finally:
        # 清理临时文件
        try:
            if input_path and os.path.exists(input_path):
                os.unlink(input_path)
                logger.info(f"清理输入文件: {input_path}")
            if output_path and os.path.exists(output_path):
                os.unlink(output_path)
                logger.info(f"清理输出文件: {output_path}")
        except Exception as cleanup_error:
            logger.warning(f"清理临时文件时出错: {cleanup_error}")

@app.get("/")
async def root():
    """
    根端点，提供服务的基本信息
    """
    logger.info("Root endpoint accessed")
    return {
        "message": "Word to PDF Converter API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "features": [
            "Word to PDF conversion",
            "Support for .docx, .doc, .rtf, .odt formats",
            "File validation and error handling"
        ]
    }

# 检查命令是否可用的辅助函数
async def check_command_available(cmd, args=[]):
    """
    检查命令是否可用
    
    Args:
        cmd: 命令名称
        args: 命令参数列表
    
    Returns:
        命令是否可用的布尔值
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False

@app.get("/health")
async def health_check():
    """
    健康检查端点，包含服务状态和功能测试
    """
    try:
        # 检查基础服务是否可用
        logger.info("执行健康检查...")
        
        # 检查 libreoffice 是否可用
        lo_available = await check_command_available("libreoffice", ["--version"])
        logger.info(f"LibreOffice 状态: {'可用' if lo_available else '不可用'}")
        
        # 检查 unoconvert 是否可用
        unoconvert_available = await check_command_available("unoconvert", ["--help"])
        logger.info(f"Unoconvert 状态: {'可用' if unoconvert_available else '不可用'}")
        
        # 检查 unoserver 连接
        unoserver_available = check_unoserver_connection()
        logger.info(f"Unoserver 连接状态: {'可用' if unoserver_available else '不可用'}")
        
        # 检查中文字体
        chinese_fonts = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "fc-list", ":lang=zh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                chinese_fonts = stdout.decode('utf-8', errors='replace').split('\n')
                chinese_fonts = [font.strip() for font in chinese_fonts if font.strip()]
            logger.info(f"检测到 {len(chinese_fonts)} 种中文字体")
        except Exception as font_check_error:
            logger.warning(f"检查中文字体失败: {font_check_error}")
        
        # 功能测试：尝试转换一个简单的测试文件
        conversion_working = False
        test_input_path = None
        test_output_path = None
        
        try:
            logger.info("执行转换功能测试...")
            # 创建一个简单的测试文件
            test_content = b"Test Document"
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_input:
                temp_input.write(test_content)
                test_input_path = temp_input.name
            
            test_output_path = test_input_path.rsplit('.', 1)[0] + '.pdf'
            
            # 设置测试环境变量
            env = os.environ.copy()
            env['LANG'] = 'zh_CN.UTF-8'
            env['LC_ALL'] = 'zh_CN.UTF-8'
            
            # 尝试使用LibreOffice进行简单转换测试
            test_cmd = [
                "libreoffice",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", os.path.dirname(test_output_path),
                test_input_path
            ]
            
            # 使用较短的超时进行测试
            await execute_command(test_cmd, env=env, timeout=30)
            
            # 验证转换结果
            if os.path.exists(test_output_path) and os.path.getsize(test_output_path) > 0:
                conversion_working = True
                logger.info("转换功能测试通过")
            else:
                logger.warning("转换功能测试失败: 输出文件为空或不存在")
                
        except Exception as conversion_test_error:
            logger.warning(f"转换功能测试失败: {conversion_test_error}")
        finally:
            # 清理测试文件
            for path in [test_input_path, test_output_path]:
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception as cleanup_error:
                        logger.warning(f"清理测试文件失败: {cleanup_error}")
        
        # 确定整体状态
        # 如果转换功能正常，且所有基础服务可用，则状态为健康
        # 否则状态为降级
        status = "healthy" if (lo_available and unoconvert_available and unoserver_available and conversion_working) else "degraded"
        chinese_fonts_available = len(chinese_fonts) > 0
        
        logger.info(f"健康检查结果: {status}")
        
        return {
            "status": status,
            "services": {
                "libreoffice": "available" if lo_available else "unavailable",
                "unoconvert": "available" if unoconvert_available else "unavailable",
                "unoserver": "available" if unoserver_available else "unavailable",
                "conversion_functionality": "working" if conversion_working else "not working"
            },
            "font_support": {
                "chinese_fonts_available": chinese_fonts_available,
                "chinese_fonts_count": len(chinese_fonts)
            },
            "locale": {
                "lang": os.environ.get('LANG', '未设置'),
                "lc_all": os.environ.get('LC_ALL', '未设置')
            },
            "timestamp": asyncio.get_event_loop().time()
        }
        
    except Exception as e:
        logger.error(f"健康检查失败: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }