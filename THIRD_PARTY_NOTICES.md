# Third-Party Notices

## code-review-graph

CodeCrow's named read-only relation queries, weighted impact-radius scoring,
and bounded graph traversal adapt portions of
[`tirth8205/code-review-graph`](https://github.com/tirth8205/code-review-graph),
release 2.3.8 at commit
[`b58668751ab0c7670c078cf7cbd4d1f5b8e54f81`](https://github.com/tirth8205/code-review-graph/commit/b58668751ab0c7670c078cf7cbd4d1f5b8e54f81).
The adapted algorithms operate over CodeCrow's existing immutable structural
store, revision receipts, tenant binding, proposed-tree overlays, and neutral
analysis-plugin facts.
CodeCrow's minimal-context selector is CodeCrow-native; the upstream project's
compact-context surface informed its design.

MIT License

Copyright (c) 2026 Tirth Kanani

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
