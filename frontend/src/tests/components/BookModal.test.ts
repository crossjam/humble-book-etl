import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import BookModal from "@/components/cards/BookModal.vue";
import type { Bundle } from "@/types/bundle";

const bundle: Bundle = {
  id: "image-comics",
  machine_name: "image-comics",
  tile_name: "Image Comics",
  product_url: "https://www.humblebundle.com/books/image-comics",
  book_list: [
    {
      machine_name: "singularity_imagecomics",
      title: "SINGULARITY",
      authors: ["Mat Groom", "Bear McCreary"],
      publishers: [
        { name: "Image Comics", url: "https://imagecomics.com/" },
      ],
      formats: ["pdf"],
      description: "A sweeping, cosmic story.",
      detail_image: "https://images.example/singularity.jpg",
      image: "https://images.example/singularity-cover.jpg",
    },
  ],
};

describe("BookModal", () => {
  it("renders detailed title metadata when expanded", async () => {
    const wrapper = mount(BookModal, { props: { bundle } });

    expect(wrapper.text()).toContain("Mat Groom, Bear McCreary");
    await wrapper.get(".book-info").trigger("click");

    expect(wrapper.text()).toContain("Image Comics");
    expect(wrapper.text()).toContain("PDF");
    expect(wrapper.text()).toContain("A sweeping, cosmic story.");
    expect(wrapper.get(".book-detail-image").attributes("src")).toBe(
      "https://images.example/singularity.jpg",
    );
  });

  it("falls back to the cover image when the detail image fails", async () => {
    const wrapper = mount(BookModal, { props: { bundle } });
    await wrapper.get(".book-info").trigger("click");

    const image = wrapper.get(".book-detail-image");
    await image.trigger("error");
    expect(image.attributes("src")).toBe(
      "https://images.example/singularity-cover.jpg",
    );

    await image.trigger("error");
    expect(image.attributes("style")).toContain("display: none");
  });
});
