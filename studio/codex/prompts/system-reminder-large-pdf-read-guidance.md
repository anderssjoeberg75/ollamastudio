<!--
name: "System Reminder: Large PDF read guidance"
description: "Warns that a PDF cannot be read all at once (too large, or the model can only be sent its pages as images) and requires reading specific page ranges with the Read tool's pages parameter"
ccVersion: "2.1.288"
variables:
  - "ESCAPE_UNTRUSTED_TEXT_FN"
  - "PDF_FILE_REFERENCE"
  - "PLURALIZE_FN"
  - "FORMAT_FILE_SIZE_FN"
  - "READ_TOOL_NAME"
-->
PDF file: ${ESCAPE_UNTRUSTED_TEXT_FN(PDF_FILE_REFERENCE.filename)} (${PDF_FILE_REFERENCE.pageCount} ${PLURALIZE_FN(PDF_FILE_REFERENCE.pageCount,"page")}, ${FORMAT_FILE_SIZE_FN(PDF_FILE_REFERENCE.fileSize)}). ${PDF_FILE_REFERENCE.wholeRefusedByModel?`This model cannot be sent a PDF file, only its pages as images${PDF_FILE_REFERENCE.pageCount>0?`; pages: "1-${PDF_FILE_REFERENCE.pageCount}" reads all of it`:""}.`:"This PDF is too large to read all at once."} You MUST use the ${READ_TOOL_NAME} tool with the pages parameter to read specific page ranges (e.g., pages: "1-5"). Do NOT call ${READ_TOOL_NAME} without the pages parameter or it will fail. 
