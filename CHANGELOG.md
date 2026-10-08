# Changelog

All notable changes to this project will be documented in this file.

The format is based on "Keep a Changelog" and this project adheres to Semantic Versioning.

## [Unreleased]
- Target data sizing: `spec.sizing` (`iterationSeconds`, `maxSeconds`,
  `margin`), the menu's `Size for a target workflow` mode, and
  `punch run --size-for <target>` size a producer run for the rows its target
  reads. After a producer switch the menu now asks before writing the
  switched dataset.
- Optional datasets: `spec.data.optional` lists datasets a workflow uses when
  present and skips otherwise.
- Initial AI-ready configuration

