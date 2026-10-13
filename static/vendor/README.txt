Libraries used by the pages, copied here so the app never depends on another website.
They are unchanged copies of the official npm packages (checked against the npm registry).

Folder          Library                 Version  License  Source
tabler/         @tabler/core            1.6.1    MIT      https://github.com/tabler/tabler
tabler-icons/   @tabler/icons-webfont   3.49.0   MIT      https://github.com/tabler/tabler-icons
sortablejs/     sortablejs              1.15.7   MIT      https://github.com/SortableJS/Sortable
frappe-gantt/   frappe-gantt            1.2.2    MIT      https://github.com/frappe/gantt
chartjs/        chart.js                4.5.1    MIT      https://github.com/chartjs/Chart.js

The license of each library is in its folder (Tabler's is at the top of its .css and .js files).
tabler/tabler.rtl.min.css (and its .map) is the same design mirrored for right-to-left languages: the app
uses it when the page is in Arabic (Stage 17c). It comes from the same npm package (dist/css/) and is unchanged.
The .map files only help browser developer tools; they are kept because the main files mention them.

To update a library: download the new version from https://www.npmjs.com/ (or
https://cdn.jsdelivr.net/npm/<name>@<version>/), replace the files, update this table,
then run "python manage.py test" and check the pages.
