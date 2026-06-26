const navToggle = document.querySelector(".nav-toggle");
const topicNav = document.querySelector("#topicNav");
const topicSearch = document.querySelector("#topicSearch");
const links = Array.from(document.querySelectorAll(".topic-nav a"));
const sections = links
  .map((link) => document.querySelector(link.getAttribute("href")))
  .filter(Boolean);

navToggle.addEventListener("click", () => {
  const isOpen = document.body.classList.toggle("nav-open");
  navToggle.setAttribute("aria-expanded", String(isOpen));
});

links.forEach((link) => {
  link.addEventListener("click", () => {
    document.body.classList.remove("nav-open");
    navToggle.setAttribute("aria-expanded", "false");
  });
});

topicSearch.addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();

  topicNav.querySelectorAll("details").forEach((group) => {
    let groupHasVisibleLink = false;
    group.querySelectorAll("a").forEach((link) => {
      const visible = link.textContent.toLowerCase().includes(query);
      link.classList.toggle("hidden-topic", query && !visible);
      groupHasVisibleLink ||= visible;
    });
    group.classList.toggle("hidden-topic", query && !groupHasVisibleLink);
    if (query && groupHasVisibleLink) group.open = true;
  });
});

const markActiveLink = () => {
  let current = sections[0];
  for (const section of sections) {
    const top = section.getBoundingClientRect().top;
    if (top <= 120) current = section;
  }

  links.forEach((link) => {
    link.classList.toggle(
      "active",
      current && link.getAttribute("href") === `#${current.id}`
    );
  });
};

document.addEventListener("scroll", markActiveLink, { passive: true });
window.addEventListener("load", markActiveLink);
