none:
	@echo make run-black
	@echo make test
	@echo make coverage

run-black:
	black -l 120 gitdiffnavtool.py testRepo.py gitrepo.py

test:
	venv-3.14/bin/python -m pytest --cov=gitrepo --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

coverage:
	venv-3.14/bin/python -m pytest --cov=gitrepo --cov-report=term-missing --cov-report=html tests/test_gitrepo.py
	@echo "HTML report: htmlcov/index.html"
