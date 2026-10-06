import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// jsdom has no native dialog top layer. The live browser check verifies focus/Escape.
HTMLDialogElement.prototype.showModal = function () { this.open = true; };
