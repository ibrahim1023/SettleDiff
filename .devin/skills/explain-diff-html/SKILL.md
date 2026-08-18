---
name: explain-diff-html
description: Use when the user asks for a rich explanation of a code change, diff, branch, commit, or pull request. Produces a self-contained interactive HTML explanation.
---

# Explain Diff

Create a rich, interactive explanation of the specified code change.

The goal is not merely to summarize the diff. Explain the surrounding system, the motivation for the change, how the implementation works, and how the behavior was verified.

## Required Sections

### 1. Background

Explain the existing system relevant to the change.

Start with optional beginner-level background, then progressively narrow the explanation toward the exact code paths affected by the change.

Cover:

- The broader subsystem or feature
- The important components, files, modules, classes, functions, and data structures
- How data or control flows through the system before the change
- Contracts, invariants, assumptions, and dependencies relevant to the change
- Existing behavior that motivated or constrained the implementation

Clearly separate:

- Beginner background
- System-specific background
- Change-specific background

Readers should be able to skip the beginner material without losing the explanation of the change.

### 2. Intuition

Explain the core intuition behind the change using concrete examples and toy data.

Cover:

- The problem in plain language
- Why the previous behavior was insufficient
- The central idea behind the solution
- Before-and-after behavior
- Important edge cases
- Trade-offs introduced by the approach

Use figures and diagrams where they improve understanding.

Prefer diagrams such as:

- Before-and-after system views
- Data-flow diagrams
- State transitions
- Simplified UI representations
- Request and response flows
- Execution timelines
- Example object transformations
- Decision paths

Do not use ASCII diagrams.

### 3. Code

Give a high-level walkthrough of the implementation.

Group changes by responsibility rather than listing files in arbitrary diff order.

For each group of changes:

- Explain its purpose
- Identify the relevant file paths
- Identify important symbols, such as functions, methods, classes, types, schemas, or configuration keys
- Explain how it connects to the other changes
- Show only the smallest code excerpts needed to explain the behavior
- Explain non-obvious implementation decisions
- Call out compatibility concerns, migration behavior, or fallback paths

Do not reproduce the entire diff.

Present the walkthrough in an order that makes the implementation easy to follow, such as:

1. Data model or contract changes
2. Core behavior
3. Integration points
4. Error handling and edge cases
5. Tests and supporting utilities

### 4. Verification

Explain how the change was validated.

Cover:

- Automated tests added or updated
- Existing tests that exercise the changed behavior
- Static checks, type checks, linting, builds, or formatting checks
- Safety properties or invariants being protected
- Acceptance evidence visible in the repository
- Remaining manual validation
- Known limitations and unresolved risks

Clearly distinguish between:

- Tests that were inspected
- Tests that were actually executed
- Behavior observed directly
- Behavior inferred from code
- Planned behavior that is not yet implemented

Never claim that a test passed unless there is evidence that it was run successfully.

When a test was not executed, explicitly state that it was inspected but not run.

## Accuracy Requirements

- Never invent behavior, architectural intent, test results, performance characteristics, or validation evidence.
- Clearly label inferences and assumptions.
- Ground explanations in the actual repository, diff, tests, documentation, and surrounding code.
- Distinguish committed changes, uncommitted changes, generated files, and unrelated working-tree modifications when relevant.
- Mention conflicting or ambiguous evidence instead of silently choosing one interpretation.
- Use exact file paths and symbol names when discussing implementation details.
- Avoid claiming that the change completely solves a problem unless the repository evidence supports that conclusion.
- Distinguish current behavior from proposed, planned, or partially implemented behavior.

## Output Requirements

Produce one self-contained HTML file with:

- Inline CSS
- Inline JavaScript
- No external runtime dependencies
- One long responsive page
- A table of contents
- Linked section navigation
- Clear section headers
- Accessible typography and spacing
- Responsive diagrams and code blocks
- Print-friendly styling where practical

Do not use top-level tabs.

Write the file outside the repository at:

`/tmp/YYYY-MM-DD-explanation-<slug>.html` 

Use:

- The current date
- A short descriptive lowercase slug
- Hyphens between words

Example:

`/tmp/2026-08-05-explanation-checkpoint-null-handling.html` 

## Writing Style

Use smooth, clear prose in a classic technical-explanation style.

Prefer:

- Complete paragraphs
- Concrete examples
- Progressive disclosure
- Precise terminology
- Short definitions near first use
- Explicit connections between concepts
- Clear separation of facts, observations, and inferences

Avoid:

- Excessive one-line paragraphs
- Repeating the diff without interpretation
- Marketing language
- Unsupported claims
- Overly decorative interactions
- Dense unexplained jargon

Use callouts for:

- Definitions
- Key concepts
- Important edge cases
- Invariants
- Warnings
- Known limitations
- Inferences

## Diagrams

Use a small reusable family of HTML and CSS diagram components.

Prefer consistency over creating a different visual style for every diagram.

Useful reusable components include:

- System component cards
- Directional connectors
- Data packets
- State nodes
- Before-and-after panels
- Execution-step timelines
- Input and output examples
- Simplified UI panels
- Decision branches
- Highlighted boundaries or trust zones

Diagrams should:

- Use semantic HTML where practical
- Remain readable on mobile screens
- Include concise labels
- Include example data where relevant
- Have accompanying prose that explains what the reader should notice
- Avoid relying only on color to communicate meaning

Do not use ASCII diagrams.

## Interactions

Add lightweight interactions only when they materially improve comprehension.

Appropriate interactions include:

- Expandable beginner background
- Expandable implementation details
- Linked table-of-contents navigation
- Before-and-after comparison controls
- Buttons that reveal example data
- Collapsible code excerpts
- Highlighting corresponding steps across a diagram and explanation

Do not add:

- Quizzes
- Games
- Decorative animations
- Complex application-like navigation
- Interactions that hide essential information by default

The document must remain understandable when JavaScript is disabled.

## Code Blocks

Use `<pre>` for all code blocks.

Before saving, inspect every code block and confirm that the CSS applied to `<pre>` includes either:

```css
white-space: pre;
```

or:

```css
white-space: pre-wrap;
```

Code blocks must:

- Preserve indentation
- Scroll horizontally or wrap safely on small screens
- Use readable contrast
- Avoid overflowing the page
- Include a file path or context label when useful
- Contain only the smallest excerpt needed for the explanation

## Workflow

1. Identify the requested comparison target, such as a diff, branch, commit, working tree, or pull request.

2. Inspect the complete relevant diff.

3. Broadly explore the affected code paths, including:

   - Callers
   - Callees
   - Related types and schemas
   - Configuration
   - Tests
   - Fixtures
   - Documentation
   - Interfaces and contracts
   - Error handling
   - Adjacent behavior that constrains the change

4. Determine the existing system behavior before the change.

5. Identify the problem, motivation, and intended behavior using repository evidence.

6. Separate:

   - Beginner background
   - System-specific background
   - Change-specific background

7. Build concrete toy examples that accurately reflect the real implementation.

8. Organize the code walkthrough by responsibility and dependency order.

9. Create reusable HTML and CSS diagrams for the most important concepts.

10. Write the verification section based on actual tests, commands, evidence, and remaining risks.

11. Clearly label:

    - Observed behavior
    - Executed verification
    - Inspected but unexecuted tests
    - Inferred behavior
    - Planned behavior
    - Known limitations

12. Generate the self-contained HTML file at the required `/tmp` path.

13. Validate the final HTML:

    - It is a single file
    - CSS is inline
    - JavaScript is inline
    - There are no required external dependencies
    - Table-of-contents links work
    - Section anchors work
    - Interactive controls function in a browser
    - The page remains understandable without JavaScript
    - Layout is responsive
    - Diagrams remain readable on narrow screens
    - Code blocks do not overflow
    - Every `<pre>` has `white-space: pre` or `white-space: pre-wrap` 
    - No quiz controls are present

14. Open or inspect the generated file and correct any obvious layout, navigation, code-formatting, or content issues before finishing.

## Final Response

After creating the file, respond with:

- The exact output path
- A one-sentence description of what was explained
- Any important verification limitation, such as tests that were inspected but not run

Do not paste the full HTML into the chat unless explicitly requested.
