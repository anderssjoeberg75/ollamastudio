<!--
name: "Data: Working tree upload refusal for private git directory setup failure"
description: "Error refusing a working-tree upload because its private git directory could not be prepared under the configuration home, telling the user to check that the home and its seed-admin folder are directories only they can write, inspect them with ls -ld, remove scratch directories or links by name without a trailing slash, and retry"
ccVersion: "2.1.290"
variables:
  - "PRIVATE_GIT_DIR_FAILURE_DETAIL"
  - "FORMAT_DISPLAY_PATH_FN"
  - "PRIVATE_GIT_DIR_FAILURE"
  - "JOIN_PATH_FN"
  - "PREVIOUS_UPLOAD_METHOD_REFUSAL_NOTE"
-->
Could not prepare a private git directory for the upload (${PRIVATE_GIT_DIR_FAILURE_DETAIL}), so nothing was uploaded. It is kept under your configuration home: check that ${FORMAT_DISPLAY_PATH_FN(PRIVATE_GIT_DIR_FAILURE.home)} is a writable directory of yours, and that ${FORMAT_DISPLAY_PATH_FN(JOIN_PATH_FN(PRIVATE_GIT_DIR_FAILURE.home,"seed-admin"))}, if it exists, is a directory only you can write. Look at it first: ls -ld, with no / after the name (tab completion adds one, and with it a link to a folder shows as a plain folder). A directory there holds scratch only and can be removed (rm -r, again with no /). A link or a file there is removed by its name (rm, with no -r and no / after the name). Then retry.${PREVIOUS_UPLOAD_METHOD_REFUSAL_NOTE}
