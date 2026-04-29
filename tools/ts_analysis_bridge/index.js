#!/usr/bin/env node
/**
 * ts_analysis_bridge/index.js
 * Gemma Swarm — Semantic JS/TS analysis bridge.
 *
 * Usage:
 *   node index.js <command> --symbol <name> --root <path> [--file <path>] [--new-name <name>] [--dry-run]
 *
 * Commands:
 *   find_references     Find all references to a symbol across the project
 *   get_definition      Find where a symbol is defined
 *   rename_symbol       Rename a symbol across the project (scope-aware)
 *
 * Always outputs a single JSON object to stdout.
 * On error: { "error": "<message>" }
 * On success: command-specific JSON (see each handler below)
 */

"use strict";

const { Project, ts } = require("ts-morph");
const path = require("path");
const fs = require("fs");

// ── Argument parsing ──────────────────────────────────────────────────────────

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const val = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
      args[key] = val;
    }
  }
  return args;
}

const [, , command, ...rest] = process.argv;
const args = parseArgs(rest);

function fail(msg) {
  process.stdout.write(JSON.stringify({ error: msg }) + "\n");
  process.exit(1);
}

function succeed(data) {
  process.stdout.write(JSON.stringify(data) + "\n");
  process.exit(0);
}

// ── Project initialisation ────────────────────────────────────────────────────

function buildProject(rootPath) {
  const tsConfigPath = path.join(rootPath, "tsconfig.json");
  if (fs.existsSync(tsConfigPath)) {
    return new Project({ tsConfigFilePath: tsConfigPath, skipAddingFilesFromTsConfig: false });
  }
  // No tsconfig — add all JS/TS files manually
  const project = new Project({
    compilerOptions: {
      allowJs: true,
      resolveJsonModule: true,
      esModuleInterop: true,
    },
  });
  project.addSourceFilesAtPaths([
    path.join(rootPath, "**/*.ts"),
    path.join(rootPath, "**/*.tsx"),
    path.join(rootPath, "**/*.js"),
    path.join(rootPath, "**/*.jsx"),
  ]);
  return project;
}

function filterSkipDirs(sourceFiles) {
  const SKIP = new Set(["node_modules", ".git", "dist", "build", ".venv", "venv"]);
  return sourceFiles.filter((sf) => {
    const parts = sf.getFilePath().split(/[\\/]/);
    return !parts.some((p) => SKIP.has(p));
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Find the first identifier node matching symbolName in a source file.
 * Prefers definition sites (FunctionDeclaration, ClassDeclaration, etc.)
 * over plain references so get_definition is accurate.
 */
function findDefinitionNode(sourceFile, symbolName) {
  // Try named declarations first
  const declarations = [
    ...sourceFile.getFunctions(),
    ...sourceFile.getClasses(),
    ...sourceFile.getInterfaces(),
    ...sourceFile.getTypeAliases(),
    ...sourceFile.getVariableDeclarations(),
    ...sourceFile.getEnums(),
  ];
  for (const decl of declarations) {
    if (decl.getName && decl.getName() === symbolName) {
      return decl.getNameNode ? decl.getNameNode() : decl;
    }
  }
  // Fall back to first identifier with that name
  const identifiers = sourceFile.getDescendantsOfKind(ts.SyntaxKind.Identifier);
  return identifiers.find((id) => id.getText() === symbolName) || null;
}

// ── Command: find_references ──────────────────────────────────────────────────

function cmdFindReferences(args) {
  const { symbol, root, file } = args;
  if (!symbol) fail("--symbol is required");
  if (!root) fail("--root is required");

  const project = buildProject(root);
  const sourceFiles = filterSkipDirs(project.getSourceFiles());

  // If a specific file is given, start from there; otherwise scan all files for the definition
  const searchIn = file
    ? [project.getSourceFile(path.resolve(file))].filter(Boolean)
    : sourceFiles;

  const results = [];

  // Find the definition node — must scan ALL files when no specific file given,
  // because the symbol may be defined in a different file than where it's used.
  let definitionNode = null;
  for (const sf of searchIn) {
    const node = findDefinitionNode(sf, symbol);
    if (node) {
      definitionNode = node;
      break;
    }
  }

  if (!definitionNode) {
    succeed({ symbol, references: [] });
    return;
  }

  let refs;
  try {
    refs = definitionNode.findReferencesAsNodes();
  } catch (_) {
    refs = [];
  }

  // findReferencesAsNodes() does NOT include the declaration node itself.
  // Prepend it so the output always includes the definition site.
  const defFile = definitionNode.getSourceFile().getFilePath();
  const defLine = definitionNode.getStartLineNumber();
  const defText = definitionNode.getSourceFile().getFullText().split("\n")[defLine - 1]?.trim() || "";
  results.push({ file: defFile, line: defLine, text: defText, is_definition: true });

  for (const ref of refs) {
    const refFile = ref.getSourceFile().getFilePath();
    const line = ref.getStartLineNumber();
    if (refFile === defFile && line === defLine) continue; // skip if same as definition
    const lineText = ref.getSourceFile().getFullText().split("\n")[line - 1] || "";
    results.push({ file: refFile, line, text: lineText.trim(), is_definition: false });
  }

  // De-duplicate (same file+line can appear from multiple starting points)
  const seen = new Set();
  const unique = results.filter((r) => {
    const key = `${r.file}:${r.line}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  succeed({ symbol, references: unique });
}

// ── Command: get_definition ───────────────────────────────────────────────────

function cmdGetDefinition(args) {
  const { symbol, root } = args;
  if (!symbol) fail("--symbol is required");
  if (!root) fail("--root is required");

  const project = buildProject(root);
  const sourceFiles = filterSkipDirs(project.getSourceFiles());

  const definitions = [];

  for (const sf of sourceFiles) {
    const declarations = [
      ...sf.getFunctions(),
      ...sf.getClasses(),
      ...sf.getInterfaces(),
      ...sf.getTypeAliases(),
      ...sf.getVariableDeclarations(),
      ...sf.getEnums(),
    ];

    for (const decl of declarations) {
      if (!decl.getName || decl.getName() !== symbol) continue;

      const line = decl.getStartLineNumber();
      const kind = decl.getKindName();         // "FunctionDeclaration", "ClassDeclaration", etc.
      const lineText = sf.getFullText().split("\n")[line - 1] || "";

      definitions.push({
        file: sf.getFilePath(),
        line,
        kind,
        text: lineText.trim(),
      });
    }
  }

  succeed({ symbol, definitions });
}

// ── Command: rename_symbol ────────────────────────────────────────────────────

function cmdRenameSymbol(args) {
  const { symbol, "new-name": newName, root, "dry-run": dryRun } = args;
  if (!symbol) fail("--symbol is required");
  if (!newName) fail("--new-name is required");
  if (!root) fail("--root is required");

  const isDryRun = dryRun === true || dryRun === "true" || dryRun === "1";

  const project = buildProject(root);
  const sourceFiles = filterSkipDirs(project.getSourceFiles());

  // Find the definition node to use as the rename anchor
  let definitionNode = null;
  for (const sf of sourceFiles) {
    definitionNode = findDefinitionNode(sf, symbol);
    if (definitionNode) break;
  }

  if (!definitionNode) {
    succeed({ symbol, new_name: newName, changes: [], total: 0, dry_run: isDryRun, warning: "Symbol not found in project" });
    return;
  }

  // Collect all reference locations BEFORE renaming (for the change log)
  let refs;
  try {
    refs = definitionNode.findReferencesAsNodes();
  } catch (e) {
    fail(`Failed to find references: ${e.message}`);
  }

  // findReferencesAsNodes() returns all references but NOT the declaration node itself.
  // We manually prepend it so callers always see the definition site.
  const defFile   = definitionNode.getSourceFile().getFilePath();
  const defLine   = definitionNode.getStartLineNumber();
  const defText   = definitionNode.getSourceFile().getFullText().split("\n")[defLine - 1]?.trim() || "";
  const allRefs   = [{ file: defFile, line: defLine, text: defText, is_definition: true }];

  for (const ref of refs) {
    const refFile = ref.getSourceFile().getFilePath();
    const line    = ref.getStartLineNumber();
    // Skip if this ref points to the exact same position as the definition (avoid dupe)
    if (refFile === defFile && line === defLine) continue;
    const lineText = ref.getSourceFile().getFullText().split("\n")[line - 1] || "";
    allRefs.push({ file: refFile, line, text: lineText.trim(), is_definition: false });
  }

  if (!isDryRun) {
    // Perform the rename on all references (ts-morph handles all edge cases)
    for (const ref of refs) {
      try {
        ref.replaceWithText(newName);
      } catch (_) {
        // Some refs may become stale after earlier replacements; safe to skip
      }
    }
    // Save all modified files
    project.saveSync();
  }

  succeed({
    symbol,
    new_name: newName,
    changes: allRefs,
    total: allRefs.length,
    dry_run: isDryRun,
  });
}

// ── Dispatch ──────────────────────────────────────────────────────────────────

switch (command) {
  case "find_references":
    cmdFindReferences(args);
    break;
  case "get_definition":
    cmdGetDefinition(args);
    break;
  case "rename_symbol":
    cmdRenameSymbol(args);
    break;
  default:
    fail(`Unknown command: "${command}". Valid commands: find_references, get_definition, rename_symbol`);
}
