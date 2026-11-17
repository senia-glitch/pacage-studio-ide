/* Корень, внутри которого живёт интерфейс этапа «Идея» (Shadow DOM вкладки). */

let node = document;

export const setDomRoot = (next) => { node = next; };
export const domRoot = () => node;
