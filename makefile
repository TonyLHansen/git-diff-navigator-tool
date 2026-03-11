none:
	@echo make run-black
	@echo make test

run-black:
	black -l 120 gitdiffnavtool.py testRepo.py gitrepo.py

test:
	venv-3.14/bin/python -m pytest
