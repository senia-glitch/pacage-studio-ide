// Шим для `node --test tests/js`: Node 22+ передаёт каталог как модуль.
// CI и pytest запускают файлы по маске `*.test.mjs`, этот файл им не мешает.
import "./briefmodel.test.mjs";
import "./chainmodel.test.mjs";
import "./editor.test.mjs";
import "./geometry.test.mjs";
import "./rendering.test.mjs";
import "./savestate.test.mjs";
import "./search.test.mjs";
import "./storage.test.mjs";
import "./todo.test.mjs";
