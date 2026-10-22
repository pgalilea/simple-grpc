# Repository guidelines
`simple_grpc` is a python framework for developing web applications using grpc quickly and easily.

## Coding
- Always use typing for methods, functions and return types
- If you are asked to add a docstring, do it in Google style
- Do not remove comments with # TODO: ...
- In multi-line docstrings, add a linebreak in the beginning
- Do not use em dashes, only dashes

## Tests
- Always use the `pytest` library
- Use only the fixture `mocker` from the library `pytest-mock` to mock things