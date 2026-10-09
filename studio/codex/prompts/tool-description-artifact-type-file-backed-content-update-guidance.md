<!--
name: "Tool Description: Artifact type file-backed content update guidance"
description: "Explains how to detect file-backed Artifact type content, read every changed file, republish it to the same URL, and preserve the type's index metadata"
ccVersion: "2.1.283"
variables:
  - "ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG"
  - "ARTIFACT_TYPE_FILE_STORAGE_CONFIG"
  - "FORMAT_ARTIFACT_TYPE_STORE_OVERRIDE_NOTE_FN"
  - "CAPITALIZE_FN"
  - "FORMAT_PINNED_FILE_READ_GUIDANCE_FN"
  - "FORMAT_ARTIFACT_FILE_UPDATE_PUBLISH_INSTRUCTIONS_FN"
  - "ARTIFACT_URL"
  - "PUBLISH_REFUSAL_FOLLOW_NOTE"
  - "FORMAT_ARTIFACT_STORE_WRITE_PROHIBITION_FN"
  - "ARTIFACT_CREATED_ON_FILES_MARKER"
  - "JSON_STRINGIFY_FN"
-->
List its files first, before any other call (${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.list}). This Artifact's content lives in its own files under `project/`${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeDescribed?", never in its store":""}: `${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.index}` is the index, a JSON object with ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.indexKeys}, plus a `createdOnFiles` or `convertedFrom` object; and ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.files}.${FORMAT_ARTIFACT_TYPE_STORE_OVERRIDE_NOTE_FN(ARTIFACT_TYPE_FILE_STORAGE_CONFIG,ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeDescribed)} ${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.ownFilesSeen?`${CAPITALIZE_FN(FORMAT_PINNED_FILE_READ_GUIDANCE_FN(ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.read))} Then ${FORMAT_ARTIFACT_FILE_UPDATE_PUBLISH_INSTRUCTIONS_FN(ARTIFACT_URL)}. ${PUBLISH_REFUSAL_FOLLOW_NOTE}`:`Read each file you will change (${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.read}) and ${FORMAT_ARTIFACT_FILE_UPDATE_PUBLISH_INSTRUCTIONS_FN(ARTIFACT_URL)}.`} Send the index only when you ${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.indexEdits}, keeping every other key and its `createdOnFiles` or `convertedFrom` object as ${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.ownFilesSeen?"you last wrote or read it":"read"}${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeDescribed?`; ${FORMAT_ARTIFACT_STORE_WRITE_PROHIBITION_FN(ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeCall)}${ARTIFACT_CONTENT_STORAGE_ACCESS_CONFIG.storeCall===null?"":" — reading what its old store holds, to see what was there, is fine"}. If no `${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.index}` is listed, this Artifact has not been started on files: start it on files now as you would a new one, writing the index, with ${ARTIFACT_CREATED_ON_FILES_MARKER}, and every content file under one folder and publishing them to it in ONE call (`url`: ${JSON_STRINGIFY_FN(ARTIFACT_URL)}, `root`: that folder, `file_path`: the index's absolute path, `files`: the rest by their `project/…` paths), and tell the user whether its earlier content was carried over; from then on its page shows only those files, nothing from its old store`:`. If no `${ARTIFACT_TYPE_FILE_STORAGE_CONFIG.index}` is listed, this Artifact has no files content yet: write the index, with ${ARTIFACT_CREATED_ON_FILES_MARKER}, and every content file as for a new one, under one folder, and publish them to it in ONE call (`url`: ${JSON_STRINGIFY_FN(ARTIFACT_URL)}, `root`: that folder, `file_path`: the index's absolute path, `files`: the rest by their `project/…` paths)`}
