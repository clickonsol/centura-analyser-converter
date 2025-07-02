# centura_cline_analyzer.py
import os
import re
import sys
import subprocess
import logging
from pathlib import Path
from typing import List, Optional

# Configuration
CHUNKS_DIR = Path("chunks")
SUMMARIES_DIR = Path("summaries")
PROMPTS_DIR = Path("generated_prompts")
TEMPLATE_FILE = Path("prompt_template.txt")
DEFAULT_SOURCE_FILE = "SampleCenturaCode.apt"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('analyzer.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def ensure_directories():
    """Create necessary directories if they don't exist."""
    for directory in [CHUNKS_DIR, SUMMARIES_DIR, PROMPTS_DIR]:
        directory.mkdir(exist_ok=True)
        logger.info(f"Ensured directory exists: {directory}")

def validate_requirements():
    """Validate that required files exist."""
    if not TEMPLATE_FILE.exists():
        logger.error(f"Template file not found: {TEMPLATE_FILE}")
        create_default_template()
        logger.info(f"Created default template: {TEMPLATE_FILE}")
    
    return True

def create_default_template():
    """Create a default prompt template if one doesn't exist."""
    default_template = """# Centura Function Analysis

## Task
Analyze the following Centura (SQLWindows) function and provide a detailed summary.

## Function Code
```centura
{{CODE}}
```

## Related Function Summaries
{{RELATED_SUMMARIES}}

## Analysis Required
Please provide:
1. **Function Purpose**: What does this function do?
2. **Input Parameters**: List and describe all parameters
3. **Return Values**: What does it return?
4. **Business Logic**: Describe the core business rules and logic
5. **Database Operations**: List any SQL operations or database calls
6. **Error Handling**: Describe error handling mechanisms
7. **Dependencies**: What other functions or components does it call?
8. **Side Effects**: Any global variables modified or external effects

## Output Format
Provide your analysis in clear, structured markdown format.
"""
    
    with open(TEMPLATE_FILE, 'w', encoding='utf-8') as f:
        f.write(default_template)

def chunk_centura_file(file_path: str) -> int:
    """
    Split Centura file into function chunks.
    Returns the number of chunks created.
    """
    if not Path(file_path).exists():
        logger.error(f"Source file not found: {file_path}")
        return 0
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return 0

    # Enhanced regex pattern for Centura functions
    # Matches: Function, Proc, Event Handler patterns
    function_patterns = [
        r'(?=^\s*Function\s+\w+)',  # Standard functions
        r'(?=^\s*Proc\s+\w+)',      # Procedures
        r'(?=^\s*On\s+\w+)',        # Event handlers
        r'(?=^\s*Local\s+Function\s+\w+)',  # Local functions
    ]
    
    combined_pattern = '|'.join(function_patterns)
    chunks = re.split(f'({combined_pattern})', content, flags=re.MULTILINE)
    
    # Clean and filter chunks
    valid_chunks = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk and len(chunk) > 50:  # Filter out very small chunks
            valid_chunks.append(chunk)
    
    logger.info(f"Found {len(valid_chunks)} function chunks")
    
    # Save chunks
    chunk_count = 0
    for i, chunk in enumerate(valid_chunks):
        # Extract function name for better file naming
        func_name_match = re.search(r'(?:Function|Proc|On|Local\s+Function)\s+(\w+)', chunk)
        if func_name_match:
            func_name = func_name_match.group(1)
            file_name = f"func_{i:04}_{func_name}.txt"
        else:
            file_name = f"func_{i:04}.txt"
        
        chunk_file = CHUNKS_DIR / file_name
        try:
            with open(chunk_file, "w", encoding='utf-8') as out:
                out.write(chunk)
            chunk_count += 1
            logger.debug(f"Created chunk: {file_name}")
        except Exception as e:
            logger.error(f"Error writing chunk {file_name}: {e}")
    
    logger.info(f"Successfully created {chunk_count} chunks")
    return chunk_count

def extract_function_calls(code: str) -> List[str]:
    """Extract function calls from Centura code to find dependencies."""
    # Pattern to match function calls in Centura
    call_patterns = [
        r'Call\s+(\w+)',  # Call statement
        r'(\w+)\s*\(',    # Function calls with parentheses
        r'SalSendMsg\s*\(\s*(\w+)',  # Message sends
    ]
    
    called_functions = set()
    for pattern in call_patterns:
        matches = re.findall(pattern, code, re.IGNORECASE)
        called_functions.update(matches)
    
    return list(called_functions)

def build_prompt(code_chunk_path: Path) -> Optional[Path]:
    """Build a context-aware prompt for the given code chunk."""
    try:
        with open(code_chunk_path, "r", encoding='utf-8') as f:
            code = f.read()
    except Exception as e:
        logger.error(f"Error reading chunk {code_chunk_path}: {e}")
        return None

    # Find related summaries based on function calls in the code
    called_functions = extract_function_calls(code)
    related_summaries = ""
    
    if called_functions:
        logger.debug(f"Found function calls: {called_functions}")
        
        for summary_file in SUMMARIES_DIR.glob("*.summary.txt"):
            # Check if this summary is for a called function
            summary_func_name = summary_file.stem.replace('.summary', '')
            if any(func.lower() in summary_func_name.lower() for func in called_functions):
                try:
                    with open(summary_file, "r", encoding='utf-8') as sf:
                        related_summaries += f"\n--- {summary_file.stem} ---\n" + sf.read()
                except Exception as e:
                    logger.warning(f"Error reading summary {summary_file}: {e}")

    try:
        with open(TEMPLATE_FILE, "r", encoding='utf-8') as tf:
            template = tf.read()
    except Exception as e:
        logger.error(f"Error reading template: {e}")
        return None

    prompt = template.replace("{{CODE}}", code).replace("{{RELATED_SUMMARIES}}", related_summaries.strip())

    prompt_file = PROMPTS_DIR / f"{code_chunk_path.stem}.prompt.txt"
    try:
        with open(prompt_file, "w", encoding='utf-8') as pf:
            pf.write(prompt)
        return prompt_file
    except Exception as e:
        logger.error(f"Error writing prompt {prompt_file}: {e}")
        return None

def run_cline_analysis(prompt_file: Path, output_file: Path) -> bool:
    """Run cline analysis with proper error handling."""
    try:
        logger.info(f"Running analysis for {prompt_file.name}")
        result = subprocess.run(
            ["cline", "-f", str(prompt_file)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            with open(output_file, "w", encoding='utf-8') as f:
                f.write(result.stdout)
            logger.info(f"Analysis completed: {output_file.name}")
            return True
        else:
            logger.error(f"Cline failed for {prompt_file.name}: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout running cline for {prompt_file.name}")
        return False
    except FileNotFoundError:
        logger.error("cline command not found. Please ensure it's installed and in PATH.")
        return False
    except Exception as e:
        logger.error(f"Error running cline for {prompt_file.name}: {e}")
        return False

def run(source_file: Optional[str] = None):
    """Main execution function."""
    if source_file is None:
        source_file = DEFAULT_SOURCE_FILE
    
    logger.info(f"Starting Centura analysis for: {source_file}")
    
    # Setup and validation
    ensure_directories()
    if not validate_requirements():
        return
    
    # Chunk the source file
    chunk_count = chunk_centura_file(source_file)
    if chunk_count == 0:
        logger.error("No chunks created. Exiting.")
        return
    
    # Process each chunk
    successful_analyses = 0
    total_chunks = len(list(CHUNKS_DIR.glob("func_*.txt")))
    
    for i, chunk_file in enumerate(CHUNKS_DIR.glob("func_*.txt"), 1):
        logger.info(f"Processing chunk {i}/{total_chunks}: {chunk_file.name}")
        
        prompt_file = build_prompt(chunk_file)
        if not prompt_file:
            continue
            
        output_file = SUMMARIES_DIR / f"{chunk_file.stem}.summary.txt"
        
        # Skip if summary already exists and is newer than the chunk
        if (output_file.exists() and 
            output_file.stat().st_mtime > chunk_file.stat().st_mtime):
            logger.info(f"Skipping {chunk_file.name} - summary already exists and is newer")
            successful_analyses += 1
            continue
        
        if run_cline_analysis(prompt_file, output_file):
            successful_analyses += 1
    
    logger.info(f"Analysis complete: {successful_analyses}/{total_chunks} successful")

if __name__ == "__main__":
    # Allow command line argument for source file
    source_file = sys.argv[1] if len(sys.argv) > 1 else None
    run(source_file)
