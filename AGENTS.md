# Repository guidelines

## Coding
- Always use typing for methods, functions and return types
- If you are asked to add a docstring, do it in Google style
- Do not remove comments with # TODO: ...
- In multi-line docstrings, add a linebreak in the beginning
- When creating examples, always use `tempfile.gettempdir()` in combination with `pathlib.Path.mkdir()` and `pathlib.Path.write_text()` to avoid creating proto files in the repo

## Tests
- Always use the `pytest` library
- Use only the fixture `mocker` from the library `pytest-mock` to mock things