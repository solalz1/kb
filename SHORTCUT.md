# The Share button (iPhone and Mac)

**English** · [Français](SHORTCUT.fr.md)

You build two Shortcuts (plus an optional third one for voice notes). iOS only shows, in the share sheet, the one that matches what you're sharing, so you always see **a single** KB option:

- **Add to my KB**: links and text (X, YouTube, Safari, TikTok, selected text…).
- **File to my KB**: PDFs, screenshots, photos, voice memos, videos, documents.

You need the address `https://kb.example.com/api/ingest` and your `KB_API_TOKEN`. Both can be copied from **Réglages** (Settings) in the app.

Both Shortcuts sync through iCloud: build them once on your iPhone and they also show up in the Mac's Share menu.

## Fastest: generate them (Mac, 2 minutes)

`shortcuts/build.py` writes the three Shortcuts below as files, ready to import. iOS and macOS only import **signed**
Shortcuts and only a Mac can sign them, so run it on your Mac, from the repository folder:

```bash
python3 shortcuts/build.py --url https://kb.example.com --sign
```

Then double-click each file in `shortcuts/dist/`: Shortcuts asks for your `KB_API_TOKEN` (it goes into the first
action, "Text", never into the files) and adds the Shortcut. They reach your iPhone through iCloud. They are named
`Add To KB`, `Fichier vers ma KB` and `Note perso`; rename them as you like. Without a Mac, build them by hand below.

---

## Shortcut 1: "Add to my KB" (links and text)

**Shortcuts** app → **+** → name it `Add to my KB`.

1. Tap **ⓘ (Details)** → turn on **Show in Share Sheet**. In the first line of the shortcut ("Receive … input from"), keep only **URLs**, **Safari web pages**, **Text** and **Rich text**. For "If there's no input", pick **Get Clipboard**.
2. Add **Get URLs from Input** (input: *Shortcut Input*).
3. Add **Get Item from List** → *First Item* of *URLs*.
4. Add **Get Text from Input** (input: *Shortcut Input*).
5. Optional but useful: **Ask for Input** → type *Text*, prompt `Why are you keeping this?`. You can confirm without typing anything.
   Then, to pick a folder with one tap, ask the KB for its current list (a folder you add in the app shows up here
   by itself): **Get Contents of URL** → `https://kb.example.com/api/folders/choices`, method **GET**, header
   `Authorization` = `Bearer YOUR_KB_API_TOKEN`; **Get Dictionary Value** → key `choices`; **Choose from List** on
   that value (prompt `Où le ranger ?`). The list reads `Automatique` (Claude chooses), your folders, then
   `Espace Perso` (your personal-development notes).
6. Add **Get Contents of URL**:
   - URL: `https://kb.example.com/api/ingest`
   - Tap **Show More**:
     - Method: **POST**
     - Headers: `Authorization` = `Bearer YOUR_KB_API_TOKEN`
     - Request Body: **JSON**, with three *Text* fields:
       - `url` = *Item from List* (step 3)
       - `text` = *Text* (step 4)
       - `note` = *Provided Input* (step 5), or leave empty
       - `folder` = *Chosen Item* (step 5), if you added the list
7. Add **Get Dictionary Value** → key `message` in *Contents of URL*.
8. Add **Show Notification** → *Dictionary Value*.
9. For sites that refuse servers (Medium…): add the field `page_follows` = `1` to the JSON of step 6, then
   **Get Dictionary Value** → key `page_wanted` in *Contents of URL*, and **If** *that value* **has any value**:
   - **Get Dictionary Value** → key `id` in *Contents of URL*;
   - **Get Contents of URL** → *Item from List* (step 3), method **GET** (your phone fetches the page itself);
   - **Get Contents of URL** → `https://kb.example.com/api/items/` *id* `/page`, method **POST**, the same
     `Authorization` header, Request Body **Form** with a *File* field `page` = the page you just fetched.
   - **End If**. The API only asks for this on sites that refuse it, so other shares don't wait.

Test: in the X app, tap Share on a tweet → **Add to my KB** → a confirmation appears (the server's messages are in French: « Ajouté à ta KB ✓ »).

## Shortcut 2: "File to my KB" (PDFs, images, audio, video)

1. **ⓘ** → **Show in Share Sheet**. Input types: **Images**, **PDFs**, **Files** and **Media** only.
2. Optional: **Ask for Input** → `Why are you keeping this?`, then the same folder choice as in Shortcut 1 (the three actions that end with **Choose from List**).
3. Add **Repeat with Each** item in *Shortcut Input*, and inside the loop:
   - **Get Contents of URL** → `https://kb.example.com/api/ingest`, method **POST**, same `Authorization` header.
     Request Body: **Form**, with:
     - `file` → type **File** = *Repeat Item*
     - `note` → type **Text** = *Provided Input*
     - `folder` → type **Text** = *Chosen Item*
   - **Get Dictionary Value** → key `message` in *Contents of URL*.
4. After the loop: **Show Notification** → *Repeat Results*: the server's answer for each file, including errors.

Test: in Photos, share a screenshot → **File to my KB**.

iPhone HEIC photos are converted automatically. A video over 50 MB is rejected (free storage limit): for a long video, share its link instead.

## Shortcut 3 (optional): "Personal note" (by voice)

To jot down a lesson, a principle or a thought in a few seconds, with Siri or from the Home Screen. The note goes straight to the Perso space, word for word.

1. New Shortcut `Personal note` (no need to show it in the share sheet).
2. Add **Dictate Text** (or **Ask for Input** of type *Text* if you'd rather type).
3. Optional: **Choose from Menu** with the options `Principe`, `Valeur`, `Leçon`, `Objectif`, `Habitude`, `Réflexion`, `Journal`, `Citation` (English names work too: `principle`, `value`, `lesson`, `goal`, `habit`, `quote`). Skip it and Claude picks the category.
4. Add **Get Contents of URL**:
   - URL: `https://kb.example.com/api/notes`
   - Method **POST**, same `Authorization` = `Bearer YOUR_KB_API_TOKEN` header
   - Request Body: **JSON**, *Text* fields:
     - `content` = *Dictated Text*
     - `space` = `perso`
     - `category` = *Chosen Item* (step 3), or remove this field
5. **Get Dictionary Value** → key `message`, then **Show Notification**.

Test: "Hey Siri, Personal note", dictate "I don't answer emails after 8pm", pick **Habitude**.

## On the Mac

- Both Shortcuts appear under **Share** (Safari, Finder, Preview…).
- To get them on right-click in Finder: open the Shortcut in the Mac's Shortcuts app → **ⓘ** → **Use as Quick Action** → check **Finder**.
- In any browser, you can also create a bookmark with this address:

  ```
  javascript:location.href='https://kb.example.com/add?url='+encodeURIComponent(location.href)
  ```

  It opens the app's **Ajouter** (Add) page with the link already filled in.

## Tips

- **X threads**: share the **last** tweet of a thread; everything before it is fetched. For a thread less than 7 days old, the first tweet works too.
- **Selected text** in Safari: select the passage, then Share → **Add to my KB**. The excerpt is kept with the page.
- **Sites that refuse servers** (Medium…): share as usual, from the site's app or Safari. The Shortcut has your phone
  fetch the page and sends it. Member-only stories: the full text comes only from Safari, signed in.
- **Quick idea**: run **Add to my KB** without sharing anything (from the Home Screen or Siri): it takes the clipboard. You can also dictate a note in the app.
- To go faster, drop the "Why are you keeping this?" step. You can add the note later on the card.
- **Folders**: the generated Shortcuts ask "Où le ranger ?" with the KB's folders as they are when you share, so a
  folder created in the app (**Dossiers**) is offered the next time, without rebuilding the Shortcut. `Automatique`
  lets Claude choose; an item you file by hand stays in its folder.
- **File it in Perso**: pick `Espace Perso` in "Où le ranger ?". Without that list, type `#perso` in the "Why are you keeping this?" note (e.g. `#perso #ressource to reread before my 2027 goals`). The item goes to the Perso space, and a category hashtag (`#principe`, `#valeur`, `#leçon`, `#objectif`, `#habitude`, `#réflexion`, `#journal`, `#citation`, `#ressource`, or the English `#principle`, `#value`, `#lesson`, `#goal`, `#habit`, `#quote`) files it directly. These hashtags are removed from the note.
