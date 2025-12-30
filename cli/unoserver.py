from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
import tempfile
import os
import subprocess
import asyncio
import logging
import os

# Configure logging with UTF-8 support
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Ensure proper locale settings for Chinese fonts
def setup_locale():
    """Set up locale for proper Chinese text rendering"""
    os.environ['LANG'] = 'zh_CN.UTF-8'
    os.environ['LC_ALL'] = 'zh_CN.UTF-8'
    os.environ['LC_CTYPE'] = 'zh_CN.UTF-8'
    logger.info("Locale environment variables configured for Chinese text support")

# Call setup function
setup_locale()

app = FastAPI(title="Word to PDF Converter", description="Convert Word documents to PDF using LibreOffice")

@app.post("/word_to_pdf", response_description="Converted PDF file")
async def convert_word_to_pdf(file: UploadFile = File(..., description="Word document to convert")):
    """
    Convert a Word document to PDF format with Chinese font support.

    Args:
        file: Uploaded Word document file

    Returns:
        PDF file as bytes
    """
    logger.info(f"Received file: {file.filename}, content type: {file.content_type}")

    # Validate file extension
    if not file.filename.endswith((".docx", ".doc", ".rtf", ".odt")):
        logger.warning(f"Invalid file extension: {file.filename}")
        raise HTTPException(status_code=400, detail="Only Word documents (.docx, .doc, .rtf, .odt) are supported")

    try:
        # Create temporary files
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_input:
            content = await file.read()
            logger.info(f"File size: {len(content)} bytes")
            # Check if file contains Chinese characters (simplified or traditional)
            try:
                text_content = content.decode('utf-8', errors='replace')
                has_chinese = any('\u4e00' <= char <= '\u9fff' for char in text_content)
                if has_chinese:
                    logger.info("Document contains Chinese characters")
            except Exception:
                logger.info("Could not check for Chinese characters in binary content")

            temp_input.write(content)
            input_path = temp_input.name

        # Output path with .pdf extension
        output_path = input_path.rsplit('.', 1)[0] + '.pdf'
        logger.info(f"Conversion paths - Input: {input_path}, Output: {output_path}")

        # Convert using unoserver
        try:
            # First try direct conversion with unoserver
            logger.info("Attempting conversion with unoserver")
            # Set environment variables for Chinese font support during conversion
            env = os.environ.copy()
            env['LANG'] = 'zh_CN.UTF-8'
            env['LC_ALL'] = 'zh_CN.UTF-8'

            convert_cmd = ["unoconvert", "--host-location", "remote", "--convert-to", "pdf", input_path, output_path]
            process = await asyncio.create_subprocess_exec(
                *convert_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.warning(f"Unoserver conversion attempt failed (return code: {process.returncode}), trying LibreOffice directly")
                # If unoserver fails, try direct LibreOffice conversion with font configuration
                lo_cmd = [
                    "libreoffice", "--headless", "--convert-to", "pdf",
                    "--outdir", os.path.dirname(output_path), input_path,
                    "--norestore", "--nofirststartwizard", "--nologo", "--nolockcheck"
                ]
                lo_process = await asyncio.create_subprocess_exec(
                    *lo_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
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

        # Verify output file exists and is not empty
        if not os.path.exists(output_path):
            raise Exception("Conversion failed: Output file was not created")

        if os.path.getsize(output_path) == 0:
            raise Exception("Conversion failed: Output file is empty")

        # Read the PDF file
        with open(output_path, "rb") as pdf_file:
            pdf_bytes = pdf_file.read()

        logger.info(f"Conversion successful, PDF size: {len(pdf_bytes)} bytes")

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={os.path.basename(output_path)}",
                "X-Converted-By": "unoserver-docker-api"
            }
        )

    finally:
        # Clean up temporary files
        try:
            if os.path.exists(input_path):
                os.unlink(input_path)
                logger.info(f"Cleaned up input file: {input_path}")
            if os.path.exists(output_path):
                os.unlink(output_path)
                logger.info(f"Cleaned up output file: {output_path}")
        except Exception as cleanup_error:
            logger.warning(f"Error cleaning up temporary files: {cleanup_error}")

@app.get("/")
async def root():
    """
    Root endpoint with basic information about the service.
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

@app.get("/health")
async def health_check():
    """
    Health check endpoint with status information.
    """
    # Verify that required tools are available
    try:
        # Check if libreoffice is available
        proc = await asyncio.create_subprocess_exec(
            "libreoffice", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        lo_available = proc.returncode == 0

        # Check if unoconvert is available
        proc = await asyncio.create_subprocess_exec(
            "unoconvert", "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        unoconvert_available = proc.returncode == 0

        # Check available Chinese fonts
        chinese_fonts = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "fc-list", ":lang=zh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                chinese_fonts = stdout.decode('utf-8', errors='replace').split('\n')
                chinese_fonts = [font.strip() for font in chinese_fonts if font.strip()]
        except Exception as font_check_error:
            logger.warning(f"Failed to check Chinese fonts: {font_check_error}")

        status = "healthy" if (lo_available and unoconvert_available) else "degraded"
        chinese_fonts_available = len(chinese_fonts) > 0

        logger.info(f"Health check: {status}, Chinese fonts available: {chinese_fonts_available}")
        return {
            "status": status,
            "services": {
                "libreoffice": "available" if lo_available else "unavailable",
                "unoconvert": "available" if unoconvert_available else "unavailable"
            },
            "font_support": {
                "chinese_fonts_available": chinese_fonts_available,
                "chinese_fonts_count": len(chinese_fonts)
            },
            "locale": {
                "lang": os.environ.get('LANG', 'not set'),
                "lc_all": os.environ.get('LC_ALL', 'not set')
            },
            "timestamp": asyncio.get_event_loop().time()
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }