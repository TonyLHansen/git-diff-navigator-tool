none:
	@echo make run-black
	@echo make test

run-black:
	black -l 120 gitdiffnavtool.py testRepo.py gitrepo.py

test:
	pytest
