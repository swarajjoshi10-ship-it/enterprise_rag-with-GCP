import io
import logfire
from pypdf import PdfReader, PdfWriter
from google.cloud import documentai
from app.config import settings

client = None
MAX_PAGES_PER_REQUEST = 15

def get_docai_client():
    global client
    if client is None:
        client = documentai.DocumentProcessorServiceClient()
    return client

def parse_pdf(file_path: str):
    """
    Parses a PDF with the engine selected by PDF_PARSER:
    "pypdf" (default, free, local text layer) or "docai" (Document AI OCR, billed per page).
    """
    if settings.PDF_PARSER == "docai":
        return parse_pdf_docai(file_path)
    return parse_pdf_local(file_path)

def parse_pdf_local(file_path: str):
    """
    Extracts the embedded text layer with pypdf. No OCR: scanned PDFs return little or no text.
    """
    with logfire.span("📄 Local PDF Parsing (pypdf)", filename=file_path):
        try:
            reader = PdfReader(file_path)
            full_text = "\n".join((page.extract_text() or "") for page in reader.pages)

            if not full_text.strip():
                logfire.warning(f"⚠️ pypdf found no text layer in {file_path} (scanned PDF? try PDF_PARSER=docai)")
            else:
                logfire.info(f"✅ pypdf parsed {len(reader.pages)} pages, {len(full_text)} characters")

            return full_text
        except Exception as e:
            logfire.error(f"❌ Local PDF Parse Failed: {e}")
            raise e

def parse_pdf_docai(file_path: str):
    """
    Parses PDF using Google Cloud Document AI.
    Automatically splits large PDFs into 15-page chunks to bypass synchronous API limits.
    """
    with logfire.span("📄 Document AI Parsing", filename=file_path):
        try:
            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            logfire.info(f"Total pages: {total_pages}")

            name = get_docai_client().processor_path(
                settings.PROJECT_ID, 
                settings.GCP_DOC_AI_LOCATION, 
                settings.GCP_DOC_AI_PROCESSOR_ID
            )

            full_text = ""

            # If small enough, process entirely
            if total_pages <= MAX_PAGES_PER_REQUEST:
                with open(file_path, "rb") as f:
                    image_content = f.read()
                full_text = process_document_chunk(image_content, name)
            else:
                # Split into chunks of MAX_PAGES_PER_REQUEST
                logfire.info(f"PDF exceeds {MAX_PAGES_PER_REQUEST} pages. Splitting into chunks...")
                
                for i in range(0, total_pages, MAX_PAGES_PER_REQUEST):
                    writer = PdfWriter()
                    chunk_end = min(i + MAX_PAGES_PER_REQUEST, total_pages)
                    
                    for page_num in range(i, chunk_end):
                        writer.add_page(reader.pages[page_num])
                    
                    # Write chunk to bytes
                    with io.BytesIO() as bytes_stream:
                        writer.write(bytes_stream)
                        chunk_bytes = bytes_stream.getvalue()
                        
                    with logfire.span(f"Processing pages {i+1} to {chunk_end}"):
                        chunk_text = process_document_chunk(chunk_bytes, name)
                        full_text += chunk_text + "\n"

            if not full_text.strip():
                logfire.warning(f"⚠️ Document AI returned empty text for {file_path}")
            else:
                logfire.info(f"✅ Document AI successfully parsed {len(full_text)} characters")

            return full_text

        except Exception as e:
            logfire.error(f"❌ Document AI Parse Failed: {e}")
            logfire.info("💡 Ensure the Processor ID is correct and the API is enabled.")
            raise e


def process_document_chunk(image_content: bytes, name: str) -> str:
    """Helper function to send a specific byte chunk to Document AI"""
    raw_document = documentai.RawDocument(
        content=image_content, 
        mime_type="application/pdf"
    )

    request = documentai.ProcessRequest(
        name=name, 
        raw_document=raw_document
    )

    result = get_docai_client().process_document(request=request)
    return result.document.text
