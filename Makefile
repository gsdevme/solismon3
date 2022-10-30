.PHONY: all
default: clean;

clean:
	if [ -d "$(CURDIR)/venv" ]; then rm -Rf "$(CURDIR)/venv"; fi