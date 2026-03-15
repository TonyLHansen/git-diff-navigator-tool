#!/usr/bin/env bash
# create_test_repo.sh - create a controlled git repo for fixtures
# Usage: ./scripts/create_test_repo.sh /tmp/test-repo
set -euo pipefail
OUT_INPUT=${1:-../test-repo}
case "$OUT_INPUT" in
  /*) OUT="$OUT_INPUT" ;;
  *) OUT="$(pwd)/$OUT_INPUT" ;;
esac
OUT_M="${OUT}-m"
OUT_S="${OUT}-s"
OUT_SM="${OUT}-sm"
OUT_REMOTE="${OUT}-remote.git"
echo "Using output dir: $OUT"
rm -rf "$OUT" "$OUT_M" "$OUT_S" "$OUT_SM" "$OUT_REMOTE"
mkdir -p "$OUT"
cd "$OUT"
git init -q -b main

# Keep commit metadata deterministic while still allowing callers to override it.
BASE_TS=${BASE_TS:-1765382400}
GIT_AUTHOR_NAME=${GIT_AUTHOR_NAME:-gitdiffnavtool-tests}
GIT_AUTHOR_EMAIL=${GIT_AUTHOR_EMAIL:-gitdiffnavtool-tests@example.invalid}
GIT_COMMITTER_NAME=${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}
GIT_COMMITTER_EMAIL=${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}
COMMIT_SEQ=0
commit_with_unique_ts() 
{
  local msg="$1"
  if git diff --cached --quiet; then
    return 0
  fi
  local ts=$((BASE_TS + COMMIT_SEQ))
  COMMIT_SEQ=$((COMMIT_SEQ + 1))
  GIT_AUTHOR_NAME="$GIT_AUTHOR_NAME" \
  GIT_AUTHOR_EMAIL="$GIT_AUTHOR_EMAIL" \
  GIT_COMMITTER_NAME="$GIT_COMMITTER_NAME" \
  GIT_COMMITTER_EMAIL="$GIT_COMMITTER_EMAIL" \
  GIT_AUTHOR_DATE="${ts} +0000" \
  GIT_COMMITTER_DATE="${ts} +0000" \
  git commit -m "$msg" -q
}

deterministic_commit_file_count() 
{
  local commit_num="$1"
  echo $((((commit_num * 7) + 3) % 4 + 1))
}

deterministic_file_index() 
{
  local commit_num="$1"
  local slot="$2"
  echo $((((commit_num * 17) + (slot * 23) + 11) % TOTAL + 1))
}

deterministic_file_payload() 
{
  local commit_num="$1"
  local slot="$2"
  echo $((commit_num * 1000 + slot * 37 + 19))
}

# Create directories
mkdir -p src docs bin data
# Committed files
echo "print('hello')" > src/app.py
echo "# README" > README.md
echo "LICENSE" > LICENSE
# Commit initial
git add -A
commit_with_unique_ts "initial commit"

# Create a tracked file that will be modified (tracked & modified, unstaged)
echo "original content" > docs/notes.txt
git add docs/notes.txt
commit_with_unique_ts "add notes"
# Modify it (tracked modified, unstaged)
echo "modified content" > docs/notes.txt

# Create a file and stage it (staged, not committed)
echo "staged file" > src/staged.txt
git add src/staged.txt

# Create an untracked file
echo "secret temporary" > tmpfile.tmp

# Create an executable script and commit it
echo -e "#!/usr/bin/env bash\necho run" > bin/run.sh
chmod +x bin/run.sh
git add bin/run.sh
commit_with_unique_ts "add run script"

# Create an ignored file and .gitignore
echo "tmpfile.tmp" > .gitignore
git add .gitignore
commit_with_unique_ts "add .gitignore"
# create a conflicted-like file by simulating merge state (create index entry)?
# For fixtures we'll mark a file as 'conflicted' manually in manifest if needed.

# Create many files to test paging
TOTAL=60
# create initial data files and commit them in a single batch
for i in $(seq -f "%03g" 1 ${TOTAL}); do
  echo "line $i" > data/file_${i}.txt
  git add data/file_${i}.txt
done
commit_with_unique_ts "add initial data files"

# Create many small commits that each modify 1-4 deterministic data files.
# This produces a richer commit history for scrolling tests.
for c in $(seq 1 60); do
  # choose 1-4 distinct files
  n=$(deterministic_commit_file_count "$c")
  chosen=()
  slot=1
  while [ ${#chosen[@]} -lt "$n" ]; do
    idx=$(deterministic_file_index "$c" "$slot")
    slot=$((slot + 1))
    fname="data/file_$(printf '%03d' $idx).txt"
    skip=0
    for ex in "${chosen[@]}"; do
      if [ "$ex" = "$fname" ]; then
        skip=1
        break
      fi
    done
    if [ $skip -eq 0 ]; then
      chosen+=("$fname")
    fi
  done

  slot=1
  for f in "${chosen[@]}"; do
    echo "commit ${c} deterministic $(deterministic_file_payload "$c" "$slot")" >> "$f"
    git add "$f"
    slot=$((slot + 1))
  done
  commit_with_unique_ts "mod commit ${c} - modified ${n} files"

  if [ "$c" -eq 30 ]
  then
    git init --bare -q "$OUT_REMOTE"
    git remote add origin "$OUT_REMOTE"
    git push -u -q origin main

    # Create a branch that will be merged later in history.
    git checkout -q -b feature/merged_mid_history
    echo "feature branch baseline" > docs/merged_mid_history_feature.txt
    git add docs/merged_mid_history_feature.txt
    commit_with_unique_ts "feature/merged_mid_history: add baseline feature file"
    echo "feature branch follow-up" >> docs/merged_mid_history_feature.txt
    git add docs/merged_mid_history_feature.txt
    commit_with_unique_ts "feature/merged_mid_history: extend feature file"
    git checkout -q main
  fi

  if [ "$c" -eq 45 ]
  then
    # Merge the mid-history feature branch with a deterministic timestamp.
    git merge --no-ff --no-commit feature/merged_mid_history
    commit_with_unique_ts "merge: feature/merged_mid_history into main"
  fi

  if [ "$c" -eq 50 ]
  then
    # Create a branch intentionally left open/unmerged.
    git checkout -q -b wip/open_unmerged_tail
    echo "open branch starts here" > docs/open_unmerged_tail_wip.txt
    git add docs/open_unmerged_tail_wip.txt
    commit_with_unique_ts "wip/open_unmerged_tail: add open branch marker"
    echo "open branch keeps diverging" >> docs/open_unmerged_tail_wip.txt
    git add docs/open_unmerged_tail_wip.txt
    commit_with_unique_ts "wip/open_unmerged_tail: extend unmerged work"
    git checkout -q main
  fi
done
# Add an extra data subdirectory with its own small set of files and commits
mkdir -p data/extra
for i in $(seq -f "%02g" 1 5); do
  fn="data/extra/extra_${i}.txt"
  echo "extra initial ${i}" > "$fn"
  git add "$fn"
  commit_with_unique_ts "add data/extra file ${i}"
done

# Give a subset of those files an extra commit so some have 2 commits each
for i in 1 2 3; do
  fn="data/extra/extra_$(printf '%02d' $i).txt"
  echo "followup commit on extra ${i}" >> "$fn"
  git add "$fn"
  commit_with_unique_ts "update data/extra file ${i}"
done

# Modify two more files: one at the repo top-level and one inside `data`
echo "appended top-level change" >> README.md
echo "appended data change" >> data/file_002.txt

# Stage one top-level file and one data file (staged, not committed)
git add README.md
git add data/file_002.txt

# Leave some staged files (already staged above) and some uncommitted changes
# final status
git status --porcelain > repo_status_porcelain.txt

# Create a sequence of commits that rename a file multiple times so tests
# can validate `git log --follow` semantics. We record the expected
# `git log --follow` output to `follow_test_expect.txt` for assertions.
mkdir -p follow_test
echo "first version" > follow_test/foo.txt
git add follow_test/foo.txt
commit_with_unique_ts "follow: add foo"

echo "second version" >> follow_test/foo.txt
git add follow_test/foo.txt
commit_with_unique_ts "follow: modify foo v2"

# rename foo -> bar
git mv follow_test/foo.txt follow_test/bar.txt
commit_with_unique_ts "follow: rename foo->bar"

echo "third version" >> follow_test/bar.txt
git add follow_test/bar.txt
commit_with_unique_ts "follow: modify bar v3"

# rename bar -> sub/baz (move into subdirectory)
mkdir -p follow_test/sub
git mv follow_test/bar.txt follow_test/sub/baz.txt
commit_with_unique_ts "follow: rename bar->sub/baz"

# lorem ipsum commit to create noise in history
cat > lorem.txt <<EOL
Nascetur nisl phasellus metus ultrices, consequat taciti tellus vivamus 
nibh iaculis at. Pulvinar viverra purus ipsum inceptos ante viverra ultrices 
metus massa hac est ad. Ipsum quis facilisi parturient augue fermentum 
malesuada fringilla ridiculus penatibus porttitor. Felis cum etiam risus 
ad penatibus nibh. Dictumst fringilla, euismod vulputate. Sed senectus 
congue praesent augue posuere dis. Ipsum cum mus leo erat pulvinar nisl; 
lacus pretium. Pellentesque parturient?

Quisque molestie sociis varius pellentesque dolor enim elementum? Volutpat 
hendrerit phasellus, platea facilisi. Ultrices nunc lectus nibh metus dapibus 
id molestie. Scelerisque tempus lacinia fermentum non dictum blandit aenean 
nostra vivamus neque habitasse nascetur. Justo diam potenti rutrum per egestas 
viverra. Natoque quisque cursus montes, pellentesque elementum? Imperdiet, 
condimentum ullamcorper viverra nec consectetur himenaeos. Sociosqu felis 
donec hendrerit auctor mattis natoque.

Arcu eget enim facilisi class diam posuere tincidunt gravida odio lectus 
dapibus volutpat? Tempus posuere urna vitae. Blandit purus metus ipsum 
tincidunt ornare cras penatibus. Eget ut vestibulum fringilla integer 
vestibulum venenatis sed. Arcu consectetur lectus porttitor ac turpis 
metus faucibus sollicitudin proin inceptos commodo? Amet platea maecenas 
aptent hac ultrices. Porta torquent.

Himenaeos risus sociis mi sit vitae condimentum. Tortor lectus bibendum 
aliquet vitae mauris platea metus quisque nisi sociosqu. Natoque torquent 
nec per ut adipiscing aenean. Nostra enim natoque leo rhoncus at tempor 
sagittis commodo mollis. Vehicula est commodo faucibus mauris tristique 
tincidunt fringilla aliquet, etiam dis. Etiam vel odio ridiculus, class 
nascetur netus conubia dignissim. Sollicitudin class pellentesque viverra 
himenaeos senectus montes imperdiet maecenas fames praesent rutrum. Dis 
habitant penatibus, habitant nisl ultrices sit massa. Nostra consequat 
proin.

Duis dignissim suspendisse volutpat dolor porttitor habitasse odio sed 
dui platea tempor. Elementum curabitur curae; risus imperdiet natoque 
suspendisse. Vitae curae; sollicitudin elit malesuada ad ad dui. Urna 
vulputate nostra tortor, laoreet nunc libero viverra. Habitant nec 
ullamcorper id! Leo parturient mollis varius nostra mattis velit. 
Viverra imperdiet sit diam aliquam hac placerat. Tincidunt, consequat 
hac bibendum. Ligula eleifend eros turpis sodales turpis nostra quisque 
potenti cum porttitor. Faucibus fermentum integer nisi magna condimentum, 
habitant aptent! Id eu malesuada quisque primis sociosqu primis vestibulum 
elit et class. Aptent consequat lectus facilisi auctor nisi urna.

Posuere vel pharetra natoque luctus sagittis nulla porttitor rhoncus 
molestie senectus, duis non? Felis himenaeos sagittis felis praesent 
purus euismod. Ornare commodo vehicula ut fringilla mollis lorem ridiculus 
himenaeos enim rhoncus tristique lobortis. Eget montes ullamcorper 
vulputate. Aenean porta iaculis dapibus sollicitudin nisl gravida nam 
himenaeos nisi amet lacinia a. Natoque fames facilisi felis risus euismod 
fermentum lectus. Imperdiet class pulvinar ridiculus magna ullamcorper 
varius, mus nostra sollicitudin pretium. Eros amet sagittis volutpat 
ad ridiculus eleifend aenean leo quisque. Vitae sociis blandit urna felis 
ullamcorper augue orci eleifend lacus! Dapibus per ac!

Mollis mus vitae dolor. Nisl donec lacinia sodales himenaeos. Parturient 
dapibus mollis etiam conubia tristique amet malesuada. Dolor phasellus 
libero risus hendrerit cursus libero nec, dictumst sed enim. Tempor, 
tempus tempor aenean nam donec tortor lobortis diam. Erat convallis 
vulputate dictumst ullamcorper congue mattis quisque ut quisque cras 
idiculus morbi. Nostra aliquam lacinia nisl? Blandit condimentum ornare 
montes facilisi feugiat integer donec. Mauris ut vehicula mauris orci. 
Imperdiet risus porta dictumst nascetur. Eget congue aenean nibh lobortis 
natoque aenean! Velit elit neque potenti fringilla. Velit metus venenatis 
penatibus velit penatibus est lorem nec? Lectus tellus tempor.

Torquent mus taciti in tellus condimentum. Iaculis nec urna ac cubilia 
malesuada porttitor? Lectus torquent, ipsum eleifend nisi iaculis ac 
imperdiet! Aliquam per aliquam cum. Elit himenaeos vivamus ligula montes 
etiam varius. Ligula donec scelerisque fringilla duis dui lacus egestas 
feugiat nam. Pellentesque amet sagittis ac proin pellentesque cursus purus. 
Mus interdum natoque porttitor feugiat risus ridiculus imperdiet. Molestie 
viverra neque sollicitudin posuere pellentesque tincidunt. Tincidunt 
platea bibendum tempus lacinia sapien sem id. Nec egestas nisl consequat 
condimentum sed. Ultricies hac pulvinar cubilia.

Ac dapibus praesent habitasse sagittis venenatis mauris ut erat consequat 
aliquam praesent. Cras ad auctor conubia malesuada. Pretium laoreet sit 
mus quam proin donec. Ac elit dapibus neque laoreet amet felis porttitor 
dapibus ligula non nostra. Convallis sem ridiculus sociosqu integer a ut 
sit eu sem lobortis tortor? Laoreet platea ultrices fusce. Magnis placerat 
cum per pretium eros feugiat laoreet varius senectus egestas donec volutpat. 
Velit.

Dignissim vitae blandit natoque dignissim; dictumst rhoncus massa ipsum 
ultrices. Tellus metus ultrices in nisl ornare odio cras mus magnis urna. 
Sodales magnis magnis fringilla elit elit aliquam, torquent cubilia. 
Turpis iaculis tortor diam volutpat porta etiam porttitor tellus. Elit 
fames duis consequat. Convallis placerat erat vulputate tempor egestas 
tempor curabitur pellentesque lobortis turpis fringilla montes. Sapien 
malesuada libero fermentum ridiculus nisi proin curabitur diam nulla dis. 
Rhoncus platea nisl accumsan risus aptent, platea tincidunt scelerisque 
turpis lacus.
EOL
git add lorem.txt
commit_with_unique_ts "add noise lorem ipsum commit"

cat > lorem.txt <<EOL
Nascetur nisl phasellus metus ultrices, consequat taciti tellus vivamus 
nibh iaculis at.          Pulvinar viverra purus ipsum inceptos ante viverra ultrices 
metus massa hac est ad. Ipsum quis facilisi parturient augue fermentum 
malesuada fringilla r       idiculus penatibus porttitor. Felis cum etiam risus 
ad penatibus nibh. Dict         umst fringilla, euismod vulputate. Sed senectus 
congue praesent augue posuere dis. Ipsum cum mus leo erat pulvinar nisl; 
lacus pretium. Pellentesque parturient?

Quisque molestie sociis varius pellentesque dolor enim elementum? Volutpat 
viverra. Natoque quisque cursus montes, pellentesque elementum? Imperdiet, 
condimentum ullamcorper viverra nec consectetur himenaeos. Sociosqu felis 
  hendrerit phasellus, platea facilisi. Ultrices nunc lectus nibh metus dapibus 
  id molestie. Scelerisque tempus lacinia fermentum non dictum blandit aenean 
  nostra vivamus neque habitasse nascetur. Justo diam potenti rutrum per egestas 
donec hendrerit auctor mattis natoque.

Arcu eget enim facilisi class diam posuere tincidunt gravida odio lectus 
dapibus volutpat? Tempus posuere urna vitae. Blandit purus metus ipsum 
tincidunt ornare cras penatibus. Eget ut vestibulum fringilla integer 
vestibulum venenatis sed. Arcu consectetur lectus porttitor ac turpis 
metus faucibus sollicitudin proin inceptos commodo? Amet platea maecenas 
aptent hac ultrices. Porta torquent.

Himenaeos risus sociis mi sit vitae condimentum. Tortor lectus bibendum 
himenaeos senectus montes imperdiet maecenas fames praesent rutrum. Dis 
habitant penatibus, habitant nisl ultrices sit massa. Nostra consequat 
aliquet vitae mauris platea metus quisque nisi sociosqu. Natoque torquent 
nec per ut adipiscing aenean. Nostra enim natoque leo rhoncus at tempor 
sagittis commodo mollis. Vehicula est commodo faucibus mauris tristique 
tincidunt fringilla aliquet, etiam dis. Etiam vel odio ridiculus, class 
nascetur netus conubia dignissim. Sollicitudin class pellentesque viverra 
proin.

Duis dignissim suspendisse volutpat dolor porttitor habitasse odio sed 
dui platea tempor. Elementum curabitur curae; risus imperdiet natoque 
hac bibendum. Ligula eleifend eros turpis sodales turpis nostra quisque 
potenti cum porttitor. Faucibus fermentum integer nisi magna condimentum, 
suspendisse. Vitae curae; sollicitudin elit malesuada ad ad dui. Urna 
vulputate nostra tortor, laoreet nunc libero viverra. Habitant nec 
ullamcorper id! Leo parturient mollis varius nostra mattis velit. 
Viverra imperdiet sit diam aliquam hac placerat. Tincidunt, consequat 
habitant aptent! Id eu malesuada quisque primis sociosqu primis vestibulum 
elit et class. Aptent consequat lectus facilisi auctor nisi urna.

Posuere vel pharetra natoque luctus sagittis nulla porttitor rhoncus 
molestie senectus, duis non? Felis himenaeos sagittis felis praesent 
purus euismod. Ornare commodo vehicula ut fringilla mollis lorem ridiculus 
fermentum lectus. Imperdiet class pulvinar ridiculus magna ullamcorper 
varius, mus nostra sollicitudin pretium. Eros amet sagittis volutpat 
ad ridiculus eleifend aenean leo quisque. Vitae sociis blandit urna felis 
himenaeos enim rhoncus tristique lobortis. Eget montes ullamcorper 
vulputate. Aenean porta iaculis dapibus sollicitudin nisl gravida nam 
himenaeos nisi amet lacinia a. Natoque fames facilisi felis risus euismod 
ullamcorper augue orci eleifend lacus! Dapibus per ac!

Mollis mus vitae dolor. Nisl donec lacinia sodales himenaeos. Parturient 
dapibus mollis etiam conubia tristique amet malesuada. Dolor phasellus 
libero risus hendrerit cursus libero nec, dictumst sed enim. Tempor, 
tempus tempor aenean nam donec tortor lobortis diam. Erat convallis 
vulputate dictumst ullamcorper congue mattis quisque ut quisque cras 
idiculus morbi. Nostra aliquam lacinia nisl? Blandit condimentum ornare 
montes facilisi feugiat integer donec. Mauris ut vehicula mauris orci. 
Imperdiet risus porta dictumst nascetur. Eget congue aenean nibh lobortis 
natoque aenean! Velit elit neque potenti fringilla. Velit metus venenatis 
penatibus velit penatibus est lorem nec? Lectus tellus tempor.

Torquent mus taciti in tellus condimentum. Iaculis nec urna ac cubilia 
malesuada porttitor? Lectus torquent, ipsum eleifend nisi iaculis ac 
imperdiet! Aliquam per aliquam cum. Elit himenaeos vivamus ligula montes 
etiam varius. Ligula donec scelerisque fringilla duis dui lacus egestas 
feugiat nam. Pellentesque amet sagittis ac proin pellentesque cursus purus. 
Mus interdum natoque porttitor feugiat risus ridiculus imperdiet. Molestie 
viverra neque sollicitudin posuere pellentesque tincidunt. Tincidunt 
platea bibendum tempus lacinia sapien sem id. Nec egestas nisl consequat 
condimentum sed. Ultricies hac pulvinar cubilia.

Ac dapibus praesent habitasse sagittis venenatis mauris ut erat consequat 
aliquam praesent. Cras ad auctor conubia malesuada. Pretium laoreet sit 
mus quam proin donec. Ac elit dapibus neque laoreet amet felis porttitor 
dapibus ligula non nostra. Convallis sem ridiculus sociosqu integer a ut 
sit eu sem lobortis tortor? Laoreet platea ultrices fusce. Magnis placerat 
cum per pretium eros feugiat laoreet varius senectus egestas donec volutpat. 
Velit.

Dignissim vitae blandit natoque dignissim; dictumst rhoncus massa ipsum 
ultrices. Tellus metus ultrices in nisl ornare odio cras mus magnis urna. 
Sodales magnis magnis fringilla elit elit aliquam, torquent cubilia. 
Turpis iaculis tortor diam volutpat porta etiam porttitor tellus. Elit 
fames duis consequat. Convallis placerat erat vulputate tempor egestas 
tempor curabitur pellentesque lobortis turpis fringilla montes. Sapien 
malesuada libero fermentum ridiculus nisi proin curabitur diam nulla dis. 
Rhoncus platea nisl accumsan risus aptent, platea tincidunt scelerisque 
turpis lacus.
EOL
git add lorem.txt
commit_with_unique_ts "update noise lorem ipsum commit"

echo "fourth version" >> follow_test/sub/baz.txt
git add follow_test/sub/baz.txt
commit_with_unique_ts "follow: modify baz v4"

# =====================
# Diff-variant demo file
# Create a file designed to show visibly different output when
# comparing with: `git diff`, `git diff --ignore-space-change`, and
# `git diff --diff-algorithm=patience`.
# - Commit 1: base content
# - Commit 2: whitespace-only edits (ignored by --ignore-space-change)
# - Commit 3: move a small block (patience may produce different hunks)
# - Commit 4: add repeated token to create ambiguous matches for patience
# =====================
cat > docs/diff_demo.txt <<'EOL'
alpha
beta
gamma
UNIQUE-TOKEN-12345
delta
MOVE-BLOCK-START
move: alpha
move: beta
move: gamma
MOVE-BLOCK-END
end
EOL
git add docs/diff_demo.txt
commit_with_unique_ts "add diff_demo base"

# Commit 2: whitespace-only modifications (trailing spaces and extra internal spaces)
sed -e 's/beta/beta  /' -e 's/gamma/gam  ma/' -e 's/delta/delta   /' -i.bak docs/diff_demo.txt || true
rm -f docs/diff_demo.txt.bak
git add docs/diff_demo.txt
commit_with_unique_ts "diff_demo: whitespace-only edits"

# Commit 3: move the MOVE-BLOCK to a different place (simulate a block move)
awk 'BEGIN{m=0} /MOVE-BLOCK-START/{m=1; next} /MOVE-BLOCK-END/{m=0; next} { if(m){block=block $0 "\n"} else {out=out $0 "\n"}} END{ print out "---INSERT-BEFORE---\n" block}' docs/diff_demo.txt > docs/diff_demo_moved.txt
# Replace marker with actual insertion: put moved block before the UNIQUE token
perl -0777 -pe 's/UNIQUE-TOKEN-12345/REPLACEMENT_MARKER\nUNIQUE-TOKEN-12345/s' docs/diff_demo_moved.txt > docs/diff_demo_moved2.txt
perl -0777 -pe 's/REPLACEMENT_MARKER\n---INSERT-BEFORE---\n/move: alpha\nmove: beta\nmove: gamma\n/' docs/diff_demo_moved2.txt > docs/diff_demo.txt
rm -f docs/diff_demo_moved.txt docs/diff_demo_moved2.txt
git add docs/diff_demo.txt
commit_with_unique_ts "diff_demo: moved block to create reordering"

# Commit 4: add repeated token to create ambiguous hunks for patience
echo "UNIQUE-TOKEN-12345" >> docs/diff_demo.txt
echo "UNIQUE-TOKEN-12345" >> docs/diff_demo.txt
git add docs/diff_demo.txt
commit_with_unique_ts "diff_demo: duplicate unique token to exercise patience algorithm"

# Save expected git log --follow output for the final path in two formats:
# 1) hashes only (useful for strict comparisons)
# 2) formatted lines matching the app's history output (date short + subject)
git log --follow --pretty=format:%H -- follow_test/sub/baz.txt > follow_test_expect_hashes.txt
git log --follow --pretty=format:%H\t%ad\t%s --date=short -- follow_test/sub/baz.txt > follow_test_expect.txt

# Permissions-only change: flip the executable bit without altering file contents
chmod +x follow_test/sub/baz.txt
git add follow_test/sub/baz.txt
commit_with_unique_ts "follow: permissions-only make baz executable"

# Extra committed diff_demo change kept from the older fixture repo.
echo "This is a really long line about the quick brown dog jumping over the lazy old fox. Then the lazy old fox woke up and jumped over the brown dog." >> docs/diff_demo.txt
git add docs/diff_demo.txt
commit_with_unique_ts "diff_demo: add long-line variant"

# Ensure the base repo is fully committed before creating variant copies.
git add -A
commit_with_unique_ts "finalize fixture base state"

# Leave fixture default branch on main.
git checkout -q main

# Build variant repos from the fully committed base at $OUT.
cd ..
cp -R "$OUT" "$OUT_M"
cd "$OUT_M"
echo "variant m: modified notes" >> docs/notes.txt

cd ..
cp -R "$OUT_M" "$OUT_S"
cd "$OUT_S"
git add docs/notes.txt

cd ..
cp -R "$OUT_S" "$OUT_SM"
cd "$OUT_SM"
echo "variant sm: additional unstaged notes changes" >> docs/notes.txt

printf "Created base repo at %s\n" "$OUT"
printf "Created modified repo at %s\n" "$OUT_M"
printf "Created staged repo at %s\n" "$OUT_S"
printf "Created staged+modified repo at %s\n" "$OUT_SM"
