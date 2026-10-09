<!--
name: "Tool Description: Artifact asset upload guidance"
description: "Explains uploading a local file to an artifact's asset store with asset: true, batching several uploads under one approval with file_paths, the assets capability the page must declare, and referencing an uploaded file by the url the result gives"
ccVersion: "2.1.275"
variables:
  - "MAX_BATCH_ASSET_UPLOADS"
-->
. With `url`, `file_path` and `asset: true`, it instead uploads that local image, video, PDF, font or text file to the artifact's asset store; `file_paths` in place of `file_path` uploads up to ${MAX_BATCH_ASSET_UPLOADS} image, video, PDF, font, stylesheet or script files in one call under one approval (a text file goes in a call of its own), and the result gives each one's `url`. The page must declare the `assets` capability, and the `artifact-capabilities` skill has the limits. Claude references the uploaded file from the page by the `url` in the result, exactly as given
